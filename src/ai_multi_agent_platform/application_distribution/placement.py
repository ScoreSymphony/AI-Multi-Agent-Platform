"""Build-target placement helpers over local and distributed execution inventories."""

from __future__ import annotations

import platform
import shutil
from collections.abc import Mapping
from typing import Literal

from ai_multi_agent_platform.contracts import ExecutionRequest, OperationContext
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.distributed import (
    DeterministicScheduler,
    DistributedRegistry,
    JobRequirements,
    WorkerJobRequest,
)
from ai_multi_agent_platform.domain import new_id

from .execution import APPLICATION_BUILD_ACTION
from .models import BuildSpecification, BuildTarget


class LocalBuildTargetMatcher:
    """Admit only targets the current host can truthfully execute."""

    def __init__(self, capabilities: tuple[str, ...] = ()) -> None:
        self._capabilities = frozenset(capabilities)

    async def supports(
        self,
        specification: BuildSpecification,
        target: BuildTarget,
    ) -> bool:
        os_name = _local_os()
        architecture = _local_architecture()
        if _normalize_os(target.os_name) != os_name:
            return False
        if _normalize_architecture(target.architecture) != architecture:
            return False
        command = specification.command
        if command and shutil.which(command[0]) is None:
            return False
        available = set(self._capabilities)
        available.update({f"os:{os_name}", f"arch:{architecture}"})
        if command and _python_command(command[0]):
            available.add("python")
        required = set(specification.required_capabilities)
        required.update(target.required_capabilities)
        return not (required - available)


class DistributedBuildTargetMatcher:
    """Use the canonical #14 scheduler for pre-dispatch target admission.

    This matcher is intentionally not a scheduler. It asks the same scheduler that owns remote
    placement whether at least one Worker currently satisfies the translated target requirements;
    it never reserves a Worker and the later canonical dispatch remains authoritative.
    """

    def __init__(
        self,
        registry: DistributedRegistry,
        *,
        scheduler: DeterministicScheduler | None = None,
    ) -> None:
        self.registry = registry
        self.scheduler = scheduler or DeterministicScheduler(registry)

    async def supports(
        self,
        specification: BuildSpecification,
        target: BuildTarget,
    ) -> bool:
        admission = WorkerJobRequest(
            execution=ExecutionRequest(
                run_id=new_id("run"),
                subject_type="task",
                subject_id=new_id("task"),
                context=OperationContext(correlation_id="application-build-target-admission"),
            ),
            requirements=job_requirements_for_target(specification, target),
        )
        return self.scheduler.evaluate(admission).selected_worker_id is not None


def job_requirements_for_target(
    specification: BuildSpecification,
    target: BuildTarget,
) -> JobRequirements:
    """Translate one application target into canonical #14 placement requirements.

    ``resource_hints`` remains optional/advisory metadata on ``BuildSpecification``. Only
    canonical scheduler dimensions are accepted here; unknown hints stay application metadata
    and never become an application-owned placement algorithm.
    """

    hints = specification.resource_hints
    capabilities = tuple(
        dict.fromkeys(
            (
                APPLICATION_BUILD_ACTION,
                *specification.required_capabilities,
                *target.required_capabilities,
            )
        )
    )
    return JobRequirements(
        executor_type=_optional_string(hints, "executor_type"),
        capability_refs=capabilities,
        cpu_cores_min=_number_hint(hints, "cpu_cores_min", alias="cpu_cores"),
        ram_min_bytes=_integer_hint(hints, "ram_min_bytes", alias="ram_bytes"),
        storage_min_bytes=_integer_hint(
            hints,
            "storage_min_bytes",
            alias="storage_bytes",
        ),
        gpu=_gpu_hint(hints),
        vram_min_bytes=_integer_hint(hints, "vram_min_bytes", alias="vram_bytes"),
        runtime=_optional_string(hints, "runtime"),
        os_name=_normalize_os(target.os_name),
        architecture=_normalize_architecture(target.architecture),
        network_required=_boolean_hint(hints, "network_required"),
        required_labels=_string_tuple_hint(hints, "required_labels"),
        preferred_labels=_string_tuple_hint(hints, "preferred_labels"),
        preferred_node_ids=_string_tuple_hint(hints, "preferred_node_ids"),
        preferred_worker_ids=_string_tuple_hint(hints, "preferred_worker_ids"),
        anti_affinity_node_ids=_string_tuple_hint(hints, "anti_affinity_node_ids"),
        allowed_trust_levels=_string_tuple_hint(hints, "allowed_trust_levels"),
        locality_refs=_string_tuple_hint(hints, "locality_refs"),
        concurrency_units=_positive_integer_hint(hints, "concurrency_units", default=1),
    )


def _local_os() -> str:
    return _normalize_os(platform.system())


def _local_architecture() -> str:
    return _normalize_architecture(platform.machine())


def _normalize_os(value: str) -> str:
    normalized = value.strip().lower()
    return {
        "darwin": "macos",
        "mac": "macos",
        "macos": "macos",
        "win32": "windows",
        "windows": "windows",
        "linux": "linux",
    }.get(normalized, normalized)


def _normalize_architecture(value: str) -> str:
    normalized = value.strip().lower()
    return {
        "amd64": "x86_64",
        "x64": "x86_64",
        "x86-64": "x86_64",
        "x86_64": "x86_64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }.get(normalized, normalized)


def _python_command(value: str) -> bool:
    name = value.replace("\\", "/").rsplit("/", 1)[-1].lower()
    return name in {"python", "python3", "python.exe"} or name.startswith("python3.")


def _optional_string(hints: Mapping[str, JsonValue], key: str) -> str | None:
    value = hints.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"build resource_hints.{key} must be a non-blank string")
    return value


def _number_hint(
    hints: Mapping[str, JsonValue],
    key: str,
    *,
    alias: str | None = None,
) -> float:
    value = _hint_value(hints, key, alias)
    if value is None:
        return 0.0
    if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
        raise ValueError(f"build resource_hints.{key} must be a non-negative number")
    return float(value)


def _integer_hint(
    hints: Mapping[str, JsonValue],
    key: str,
    *,
    alias: str | None = None,
) -> int:
    value = _hint_value(hints, key, alias)
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"build resource_hints.{key} must be a non-negative integer")
    return value


def _positive_integer_hint(
    hints: Mapping[str, JsonValue],
    key: str,
    *,
    default: int,
) -> int:
    value = hints.get(key)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"build resource_hints.{key} must be a positive integer")
    return value


def _boolean_hint(hints: Mapping[str, JsonValue], key: str) -> bool:
    value = hints.get(key)
    if value is None:
        return False
    if not isinstance(value, bool):
        raise ValueError(f"build resource_hints.{key} must be a boolean")
    return value


def _gpu_hint(
    hints: Mapping[str, JsonValue],
) -> Literal["optional", "required", "forbidden"]:
    value = hints.get("gpu")
    if value is None:
        return "optional"
    if not isinstance(value, str) or value not in {"optional", "required", "forbidden"}:
        raise ValueError("build resource_hints.gpu must be one of optional, required or forbidden")
    if value == "required":
        return "required"
    if value == "forbidden":
        return "forbidden"
    return "optional"


def _string_tuple_hint(hints: Mapping[str, JsonValue], key: str) -> tuple[str, ...]:
    value = hints.get(key)
    if value is None:
        return ()
    if not isinstance(value, list | tuple) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValueError(f"build resource_hints.{key} must be an array of non-blank strings")
    unique: dict[str, None] = {}
    for item in value:
        if isinstance(item, str):
            unique.setdefault(item, None)
    return tuple(unique)


def _hint_value(
    hints: Mapping[str, JsonValue],
    key: str,
    alias: str | None,
) -> JsonValue | None:
    if key in hints and alias is not None and alias in hints:
        raise ValueError(f"build resource_hints cannot define both {key} and {alias}")
    if key in hints:
        return hints[key]
    if alias is not None:
        return hints.get(alias)
    return None


__all__ = [
    "DistributedBuildTargetMatcher",
    "LocalBuildTargetMatcher",
    "job_requirements_for_target",
]
