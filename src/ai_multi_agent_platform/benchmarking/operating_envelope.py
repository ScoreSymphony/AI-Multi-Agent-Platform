"""Comparable operating-envelope analysis for issue #440 benchmark evidence."""

from __future__ import annotations

import hashlib
import json
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, cast

OPERATING_ENVELOPE_REPORT_SCHEMA_VERSION = "1.0"
_CLAIM_SEMANTICS = "tested-envelope-only"
_BUDGET_STATUS = "not-established"
_SWEEP_BENCHMARK_ID = "single-node.reference.lifecycle.sweep"
_SWEEP_SCHEMA_VERSION = "1.0"
_ENDURANCE_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True, slots=True)
class SweepConfiguration:
    """Comparable configuration shared by all accepted sweep evidence."""

    operation_count_per_point: int
    warmup_operations: int
    timeout_seconds: float
    concurrency_levels: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class ConcurrencyEnvelope:
    """Aggregate evidence for one tested concurrency level."""

    concurrency: int
    sample_count: int
    throughput_min_operations_per_second: float
    throughput_median_operations_per_second: float
    throughput_max_operations_per_second: float
    p95_latency_min_ms: float
    p95_latency_median_ms: float
    p95_latency_max_ms: float
    duration_median_seconds: float
    completed_operations_min: int
    storage_growth_median_bytes: float
    correctness_passed: bool


@dataclass(frozen=True, slots=True)
class EnduranceEvidence:
    """One accepted soak report summarized without inventing a budget."""

    source: str
    duration_seconds: float
    concurrency: int
    max_operations: int
    completed_operations: int
    throughput_operations_per_second: float
    p95_latency_ms: float
    resource_snapshot_count: int
    traced_memory_growth_bytes: int
    peak_rss_growth_bytes: int | None
    open_file_descriptor_growth: int | None
    storage_growth_bytes: int
    latency_drift_ratio: float | None
    stop_reason: str
    correctness_passed: bool


@dataclass(frozen=True, slots=True)
class OperatingEnvelopeReport:
    """Machine-readable tested envelope derived from comparable benchmark reports."""

    schema_version: str
    benchmark_id: str
    benchmark_version: str
    platform_version: str
    platform_commit: str
    generated_at: str
    deployment_profile: str
    persistence_profile: str
    workload_distribution: str
    environment: Mapping[str, Any]
    environment_fingerprint_sha256: str
    sweep_configuration: SweepConfiguration
    sweep_sources: tuple[str, ...]
    endurance_sources: tuple[str, ...]
    concurrency_envelope: tuple[ConcurrencyEnvelope, ...]
    highest_verified_concurrency: int
    endurance_evidence: tuple[EnduranceEvidence, ...]
    longest_verified_endurance_seconds: float
    claim_semantics: str
    budget_status: str
    correctness_passed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "benchmark_id": self.benchmark_id,
            "benchmark_version": self.benchmark_version,
            "platform_version": self.platform_version,
            "platform_commit": self.platform_commit,
            "generated_at": self.generated_at,
            "deployment_profile": self.deployment_profile,
            "persistence_profile": self.persistence_profile,
            "workload_distribution": self.workload_distribution,
            "environment": dict(self.environment),
            "environment_fingerprint_sha256": self.environment_fingerprint_sha256,
            "sweep_configuration": {
                "operation_count_per_point": self.sweep_configuration.operation_count_per_point,
                "warmup_operations": self.sweep_configuration.warmup_operations,
                "timeout_seconds": self.sweep_configuration.timeout_seconds,
                "concurrency_levels": list(self.sweep_configuration.concurrency_levels),
            },
            "sweep_sources": list(self.sweep_sources),
            "endurance_sources": list(self.endurance_sources),
            "concurrency_envelope": [asdict(point) for point in self.concurrency_envelope],
            "highest_verified_concurrency": self.highest_verified_concurrency,
            "endurance_evidence": [asdict(run) for run in self.endurance_evidence],
            "longest_verified_endurance_seconds": self.longest_verified_endurance_seconds,
            "claim_semantics": self.claim_semantics,
            "budget_status": self.budget_status,
            "correctness_passed": self.correctness_passed,
        }


@dataclass(frozen=True, slots=True)
class _SweepPointInput:
    concurrency: int
    repetition: int
    throughput: float
    p95_latency_ms: float
    duration_seconds: float
    completed_operations: int
    storage_growth_bytes: int


@dataclass(frozen=True, slots=True)
class _SweepInput:
    platform_version: str
    platform_commit: str
    deployment_profile: str
    persistence_profile: str
    workload_distribution: str
    environment: Mapping[str, Any]
    configuration: SweepConfiguration
    points: tuple[_SweepPointInput, ...]


class OperatingEnvelopeAnalyzer:
    """Validate and aggregate comparable sweep and soak reports."""

    def analyze(
        self,
        *,
        sweep_reports: Sequence[Mapping[str, Any]],
        endurance_reports: Sequence[Mapping[str, Any]] = (),
        sweep_sources: Sequence[str] | None = None,
        endurance_sources: Sequence[str] | None = None,
    ) -> OperatingEnvelopeReport:
        if not sweep_reports:
            raise ValueError("at least one sweep report is required")

        normalized_sweep_sources = _normalize_sources(
            sweep_sources,
            count=len(sweep_reports),
            prefix="sweep",
        )
        normalized_endurance_sources = _normalize_sources(
            endurance_sources,
            count=len(endurance_reports),
            prefix="endurance",
        )

        parsed_sweeps = tuple(
            _parse_sweep(report, source=source)
            for report, source in zip(sweep_reports, normalized_sweep_sources, strict=True)
        )
        reference = parsed_sweeps[0]
        for evidence, source in zip(
            parsed_sweeps[1:],
            normalized_sweep_sources[1:],
            strict=True,
        ):
            _require_same_sweep_basis(reference, evidence, source=source)

        points_by_concurrency: dict[int, list[_SweepPointInput]] = {
            level: [] for level in reference.configuration.concurrency_levels
        }
        for evidence in parsed_sweeps:
            for point in evidence.points:
                points_by_concurrency[point.concurrency].append(point)

        concurrency_envelope = tuple(
            _aggregate_concurrency(level, points_by_concurrency[level])
            for level in reference.configuration.concurrency_levels
        )

        endurance_evidence = tuple(
            _parse_endurance(
                report,
                source=source,
                reference=reference,
            )
            for report, source in zip(
                endurance_reports,
                normalized_endurance_sources,
                strict=True,
            )
        )
        longest_endurance = max(
            (evidence.duration_seconds for evidence in endurance_evidence),
            default=0.0,
        )

        return OperatingEnvelopeReport(
            schema_version=OPERATING_ENVELOPE_REPORT_SCHEMA_VERSION,
            benchmark_id="single-node.reference.operating-envelope.analysis",
            benchmark_version="1.0",
            platform_version=reference.platform_version,
            platform_commit=reference.platform_commit,
            generated_at=datetime.now(UTC).isoformat(),
            deployment_profile=reference.deployment_profile,
            persistence_profile=reference.persistence_profile,
            workload_distribution=reference.workload_distribution,
            environment=reference.environment,
            environment_fingerprint_sha256=_environment_fingerprint(reference.environment),
            sweep_configuration=reference.configuration,
            sweep_sources=normalized_sweep_sources,
            endurance_sources=normalized_endurance_sources,
            concurrency_envelope=concurrency_envelope,
            highest_verified_concurrency=max(reference.configuration.concurrency_levels),
            endurance_evidence=endurance_evidence,
            longest_verified_endurance_seconds=longest_endurance,
            claim_semantics=_CLAIM_SEMANTICS,
            budget_status=_BUDGET_STATUS,
            correctness_passed=True,
        )


def _parse_sweep(report: Mapping[str, Any], *, source: str) -> _SweepInput:
    if _require_str(report, "schema_version") != _SWEEP_SCHEMA_VERSION:
        raise ValueError(f"{source}: unsupported sweep schema_version")
    if _require_str(report, "benchmark_id") != _SWEEP_BENCHMARK_ID:
        raise ValueError(f"{source}: unsupported sweep benchmark_id")
    if not _require_bool(report, "correctness_passed"):
        raise ValueError(f"{source}: sweep correctness did not pass")
    if _require_string_list(report, "errors"):
        raise ValueError(f"{source}: sweep report contains errors")

    concurrency_levels = _require_int_list(report, "concurrency_levels")
    if not concurrency_levels:
        raise ValueError(f"{source}: sweep has no concurrency levels")
    if len(set(concurrency_levels)) != len(concurrency_levels):
        raise ValueError(f"{source}: concurrency levels must be unique")
    if any(level < 1 for level in concurrency_levels):
        raise ValueError(f"{source}: concurrency levels must be positive")

    repetitions = _require_int(report, "repetitions")
    if repetitions < 1:
        raise ValueError(f"{source}: repetitions must be at least 1")
    configuration = SweepConfiguration(
        operation_count_per_point=_require_int(report, "operation_count_per_point"),
        warmup_operations=_require_int(report, "warmup_operations"),
        timeout_seconds=_require_number(report, "timeout_seconds"),
        concurrency_levels=concurrency_levels,
    )

    raw_points = _require_list(report, "points")
    expected_repetitions = set(range(1, repetitions + 1))
    seen: dict[int, set[int]] = {level: set() for level in concurrency_levels}
    points: list[_SweepPointInput] = []
    for index, raw_point in enumerate(raw_points):
        point = _require_mapping(raw_point, f"{source}: points[{index}]")
        concurrency = _require_int(point, "concurrency")
        repetition = _require_int(point, "repetition")
        if concurrency not in seen:
            raise ValueError(f"{source}: point uses undeclared concurrency {concurrency}")
        if repetition in seen[concurrency]:
            raise ValueError(
                f"{source}: duplicate repetition {repetition} at concurrency {concurrency}"
            )
        if not _require_bool(point, "correctness_passed"):
            raise ValueError(
                f"{source}: correctness failed at concurrency={concurrency} repetition={repetition}"
            )
        seen[concurrency].add(repetition)
        points.append(
            _SweepPointInput(
                concurrency=concurrency,
                repetition=repetition,
                throughput=_require_number(point, "throughput_operations_per_second"),
                p95_latency_ms=_require_number(point, "p95_latency_ms"),
                duration_seconds=_require_number(point, "duration_seconds"),
                completed_operations=_require_int(point, "completed_operations"),
                storage_growth_bytes=_require_int(point, "storage_growth_bytes"),
            )
        )

    for concurrency, repetitions_seen in seen.items():
        if repetitions_seen != expected_repetitions:
            raise ValueError(
                f"{source}: incomplete repetitions at concurrency={concurrency}; "
                f"expected {sorted(expected_repetitions)}, got {sorted(repetitions_seen)}"
            )

    environment = dict(_require_mapping(report.get("environment"), f"{source}: environment"))
    return _SweepInput(
        platform_version=_require_str(report, "platform_version"),
        platform_commit=_require_str(report, "platform_commit"),
        deployment_profile=_require_str(report, "deployment_profile"),
        persistence_profile=_require_str(report, "persistence_profile"),
        workload_distribution=_require_str(report, "workload_distribution"),
        environment=environment,
        configuration=configuration,
        points=tuple(points),
    )


def _require_same_sweep_basis(
    reference: _SweepInput,
    candidate: _SweepInput,
    *,
    source: str,
) -> None:
    fields = (
        ("platform_version", reference.platform_version, candidate.platform_version),
        ("platform_commit", reference.platform_commit, candidate.platform_commit),
        ("deployment_profile", reference.deployment_profile, candidate.deployment_profile),
        ("persistence_profile", reference.persistence_profile, candidate.persistence_profile),
        (
            "workload_distribution",
            reference.workload_distribution,
            candidate.workload_distribution,
        ),
        ("configuration", reference.configuration, candidate.configuration),
    )
    for field, expected, actual in fields:
        if actual != expected:
            raise ValueError(
                f"{source}: incomparable {field}; expected {expected!r}, got {actual!r}"
            )
    if dict(candidate.environment) != dict(reference.environment):
        raise ValueError(f"{source}: incomparable environment metadata")


def _aggregate_concurrency(
    concurrency: int,
    points: Sequence[_SweepPointInput],
) -> ConcurrencyEnvelope:
    if not points:
        raise ValueError(f"no points available for concurrency {concurrency}")
    throughput = [point.throughput for point in points]
    p95_latency = [point.p95_latency_ms for point in points]
    duration = [point.duration_seconds for point in points]
    storage_growth = [float(point.storage_growth_bytes) for point in points]
    return ConcurrencyEnvelope(
        concurrency=concurrency,
        sample_count=len(points),
        throughput_min_operations_per_second=min(throughput),
        throughput_median_operations_per_second=float(statistics.median(throughput)),
        throughput_max_operations_per_second=max(throughput),
        p95_latency_min_ms=min(p95_latency),
        p95_latency_median_ms=float(statistics.median(p95_latency)),
        p95_latency_max_ms=max(p95_latency),
        duration_median_seconds=float(statistics.median(duration)),
        completed_operations_min=min(point.completed_operations for point in points),
        storage_growth_median_bytes=float(statistics.median(storage_growth)),
        correctness_passed=True,
    )


def _parse_endurance(
    report: Mapping[str, Any],
    *,
    source: str,
    reference: _SweepInput,
) -> EnduranceEvidence:
    if _require_str(report, "schema_version") != _ENDURANCE_SCHEMA_VERSION:
        raise ValueError(f"{source}: unsupported endurance schema_version")
    if _require_str(report, "platform_version") != reference.platform_version:
        raise ValueError(f"{source}: incomparable platform_version")
    if _require_str(report, "platform_commit") != reference.platform_commit:
        raise ValueError(f"{source}: incomparable platform_commit")
    environment = dict(_require_mapping(report.get("environment"), f"{source}: environment"))
    if environment != dict(reference.environment):
        raise ValueError(f"{source}: incomparable environment metadata")
    if _require_string_list(report, "errors"):
        raise ValueError(f"{source}: endurance report contains errors")

    benchmark = _require_mapping(report.get("benchmark"), f"{source}: benchmark")
    if _require_str(benchmark, "scenario") != "soak":
        raise ValueError(f"{source}: operating-envelope endurance evidence must be scenario=soak")
    if _require_str(benchmark, "deployment_profile") != reference.deployment_profile:
        raise ValueError(f"{source}: incomparable deployment_profile")
    if _require_str(benchmark, "persistence_profile") != reference.persistence_profile:
        raise ValueError(f"{source}: incomparable persistence_profile")

    correctness = _require_mapping(report.get("correctness"), f"{source}: correctness")
    if not _require_bool(correctness, "passed"):
        raise ValueError(f"{source}: endurance correctness did not pass")
    measurements = _require_mapping(report.get("measurements"), f"{source}: measurements")
    operation_latency = _require_mapping(
        report.get("operation_latency"),
        f"{source}: operation_latency",
    )

    return EnduranceEvidence(
        source=source,
        duration_seconds=_require_number(report, "duration_seconds"),
        concurrency=_require_int(benchmark, "concurrency"),
        max_operations=_require_int(benchmark, "max_operations"),
        completed_operations=_require_int(correctness, "completed_operations"),
        throughput_operations_per_second=_require_number(
            report,
            "throughput_operations_per_second",
        ),
        p95_latency_ms=_require_number(operation_latency, "p95_ms"),
        resource_snapshot_count=_require_int(measurements, "resource_snapshot_count"),
        traced_memory_growth_bytes=_require_int(measurements, "traced_memory_growth_bytes"),
        peak_rss_growth_bytes=_optional_int(measurements, "peak_rss_growth_bytes"),
        open_file_descriptor_growth=_optional_int(
            measurements,
            "open_file_descriptor_growth",
        ),
        storage_growth_bytes=_require_int(measurements, "storage_growth_bytes"),
        latency_drift_ratio=_optional_number(measurements, "latency_drift_ratio"),
        stop_reason=_require_str(measurements, "stop_reason"),
        correctness_passed=True,
    )


def _environment_fingerprint(environment: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(environment),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalize_sources(
    sources: Sequence[str] | None,
    *,
    count: int,
    prefix: str,
) -> tuple[str, ...]:
    if sources is None:
        return tuple(f"{prefix}-{index}.json" for index in range(1, count + 1))
    normalized = tuple(sources)
    if len(normalized) != count:
        raise ValueError(f"{prefix}_sources must match report count")
    if any(not source.strip() for source in normalized):
        raise ValueError(f"{prefix}_sources must not contain empty paths")
    return normalized


def _require_mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    return cast(Mapping[str, Any], value)


def _require_list(mapping: Mapping[str, Any], key: str) -> list[Any]:
    value = mapping.get(key)
    if not isinstance(value, list):
        raise ValueError(f"{key} must be an array")
    return value


def _require_string_list(mapping: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = _require_list(mapping, key)
    if any(not isinstance(item, str) for item in value):
        raise ValueError(f"{key} must contain only strings")
    return tuple(cast(str, item) for item in value)


def _require_int_list(mapping: Mapping[str, Any], key: str) -> tuple[int, ...]:
    value = _require_list(mapping, key)
    if any(isinstance(item, bool) or not isinstance(item, int) for item in value):
        raise ValueError(f"{key} must contain only integers")
    return tuple(cast(int, item) for item in value)


def _require_str(mapping: Mapping[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _require_bool(mapping: Mapping[str, Any], key: str) -> bool:
    value = mapping.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean")
    return value


def _require_int(mapping: Mapping[str, Any], key: str) -> int:
    value = mapping.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer")
    return value


def _require_number(mapping: Mapping[str, Any], key: str) -> float:
    value = mapping.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{key} must be a number")
    return float(value)


def _optional_int(mapping: Mapping[str, Any], key: str) -> int | None:
    value = mapping.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer or null")
    return value


def _optional_number(mapping: Mapping[str, Any], key: str) -> float | None:
    value = mapping.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{key} must be a number or null")
    return float(value)
