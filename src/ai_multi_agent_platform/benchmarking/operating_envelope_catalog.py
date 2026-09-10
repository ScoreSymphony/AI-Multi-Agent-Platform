"""Cross-host cataloging for issue #440 tested operating-envelope evidence."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from functools import lru_cache
from importlib.resources import files
from typing import Any, cast

from jsonschema import Draft202012Validator

OPERATING_ENVELOPE_CATALOG_SCHEMA_VERSION = "1.0"
_ENVELOPE_SCHEMA_VERSION = "1.0"
_ENVELOPE_BENCHMARK_ID = "single-node.reference.operating-envelope.analysis"
_ENVELOPE_BENCHMARK_VERSION = "1.0"
_ENVELOPE_CLAIM_SEMANTICS = "tested-envelope-only"
_CATALOG_CLAIM_SEMANTICS = "per-host-tested-envelopes-only"
_CROSS_HOST_AGGREGATION = "not-performed"
_BUDGET_STATUS = "not-established"


@dataclass(frozen=True, slots=True)
class CrossHostEnvelopeBasis:
    """Benchmark dimensions that must match before host evidence is cataloged together."""

    platform_version: str
    platform_commit: str
    deployment_profile: str
    persistence_profile: str
    workload_distribution: str
    operation_count_per_point: int
    warmup_operations: int
    timeout_seconds: float


@dataclass(frozen=True, slots=True)
class HostEnvelopeEvidence:
    """One host-specific tested envelope retained without cross-host metric aggregation."""

    label: str
    source: str
    source_sha256: str
    environment_fingerprint_sha256: str
    environment: Mapping[str, Any]
    concurrency_levels: tuple[int, ...]
    highest_verified_concurrency: int
    longest_verified_endurance_seconds: float
    concurrency_envelope: tuple[Mapping[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "source": self.source,
            "source_sha256": self.source_sha256,
            "environment_fingerprint_sha256": self.environment_fingerprint_sha256,
            "environment": dict(self.environment),
            "concurrency_levels": list(self.concurrency_levels),
            "highest_verified_concurrency": self.highest_verified_concurrency,
            "longest_verified_endurance_seconds": self.longest_verified_endurance_seconds,
            "concurrency_envelope": [dict(point) for point in self.concurrency_envelope],
        }


@dataclass(frozen=True, slots=True)
class OperatingEnvelopeCatalogReport:
    """Machine-readable catalog of independently tested host envelopes."""

    schema_version: str
    benchmark_id: str
    benchmark_version: str
    generated_at: str
    basis: CrossHostEnvelopeBasis
    host_count: int
    distinct_environment_count: int
    union_concurrency_levels: tuple[int, ...]
    shared_concurrency_levels: tuple[int, ...]
    comparison_status: str
    cross_host_comparison_ready: bool
    claim_semantics: str
    cross_host_aggregation: str
    budget_status: str
    hosts: tuple[HostEnvelopeEvidence, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "benchmark_id": self.benchmark_id,
            "benchmark_version": self.benchmark_version,
            "generated_at": self.generated_at,
            "basis": asdict(self.basis),
            "host_count": self.host_count,
            "distinct_environment_count": self.distinct_environment_count,
            "union_concurrency_levels": list(self.union_concurrency_levels),
            "shared_concurrency_levels": list(self.shared_concurrency_levels),
            "comparison_status": self.comparison_status,
            "cross_host_comparison_ready": self.cross_host_comparison_ready,
            "claim_semantics": self.claim_semantics,
            "cross_host_aggregation": self.cross_host_aggregation,
            "budget_status": self.budget_status,
            "hosts": [host.to_dict() for host in self.hosts],
        }


@dataclass(frozen=True, slots=True)
class _EnvelopeInput:
    basis: CrossHostEnvelopeBasis
    environment: Mapping[str, Any]
    environment_fingerprint_sha256: str
    concurrency_levels: tuple[int, ...]
    highest_verified_concurrency: int
    longest_verified_endurance_seconds: float
    concurrency_envelope: tuple[Mapping[str, Any], ...]


class OperatingEnvelopeCatalogBuilder:
    """Validate and catalog host-specific operating envelopes without averaging hosts."""

    def build(
        self,
        *,
        envelopes: Sequence[Mapping[str, Any]],
        labels: Sequence[str],
        sources: Sequence[str] | None = None,
    ) -> OperatingEnvelopeCatalogReport:
        if not envelopes:
            raise ValueError("at least one operating-envelope report is required")
        normalized_labels = _normalize_labels(labels, count=len(envelopes))
        normalized_sources = _normalize_sources(sources, count=len(envelopes))

        parsed = tuple(
            _parse_envelope(report, source=source)
            for report, source in zip(envelopes, normalized_sources, strict=True)
        )
        reference = parsed[0]
        for candidate, source in zip(parsed[1:], normalized_sources[1:], strict=True):
            _require_same_basis(reference.basis, candidate.basis, source=source)

        hosts = tuple(
            HostEnvelopeEvidence(
                label=label,
                source=source,
                source_sha256=_canonical_sha256(report),
                environment_fingerprint_sha256=evidence.environment_fingerprint_sha256,
                environment=evidence.environment,
                concurrency_levels=evidence.concurrency_levels,
                highest_verified_concurrency=evidence.highest_verified_concurrency,
                longest_verified_endurance_seconds=evidence.longest_verified_endurance_seconds,
                concurrency_envelope=evidence.concurrency_envelope,
            )
            for report, evidence, label, source in zip(
                envelopes,
                parsed,
                normalized_labels,
                normalized_sources,
                strict=True,
            )
        )

        fingerprints = {host.environment_fingerprint_sha256 for host in hosts}
        union_levels = tuple(sorted({level for host in hosts for level in host.concurrency_levels}))
        shared = set(hosts[0].concurrency_levels)
        for host in hosts[1:]:
            shared.intersection_update(host.concurrency_levels)
        shared_levels = tuple(sorted(shared))
        comparison_status = _comparison_status(
            host_count=len(hosts),
            distinct_environment_count=len(fingerprints),
            shared_concurrency_levels=shared_levels,
        )
        return OperatingEnvelopeCatalogReport(
            schema_version=OPERATING_ENVELOPE_CATALOG_SCHEMA_VERSION,
            benchmark_id="single-node.reference.operating-envelope.catalog",
            benchmark_version="1.0",
            generated_at=datetime.now(UTC).isoformat(),
            basis=reference.basis,
            host_count=len(hosts),
            distinct_environment_count=len(fingerprints),
            union_concurrency_levels=union_levels,
            shared_concurrency_levels=shared_levels,
            comparison_status=comparison_status,
            cross_host_comparison_ready=comparison_status == "cross-host-comparable",
            claim_semantics=_CATALOG_CLAIM_SEMANTICS,
            cross_host_aggregation=_CROSS_HOST_AGGREGATION,
            budget_status=_BUDGET_STATUS,
            hosts=hosts,
        )


def _parse_envelope(report: Mapping[str, Any], *, source: str) -> _EnvelopeInput:
    _validate_source_envelope(report, source=source)

    if _require_str(report, "schema_version") != _ENVELOPE_SCHEMA_VERSION:
        raise ValueError(f"{source}: unsupported operating-envelope schema_version")
    if _require_str(report, "benchmark_id") != _ENVELOPE_BENCHMARK_ID:
        raise ValueError(f"{source}: unsupported operating-envelope benchmark_id")
    if _require_str(report, "benchmark_version") != _ENVELOPE_BENCHMARK_VERSION:
        raise ValueError(f"{source}: unsupported operating-envelope benchmark_version")
    if _require_str(report, "claim_semantics") != _ENVELOPE_CLAIM_SEMANTICS:
        raise ValueError(f"{source}: unsupported claim_semantics")
    if _require_str(report, "budget_status") != _BUDGET_STATUS:
        raise ValueError(f"{source}: unsupported budget_status")
    if not _require_bool(report, "correctness_passed"):
        raise ValueError(f"{source}: operating-envelope correctness did not pass")

    environment = dict(_require_mapping(report.get("environment"), f"{source}: environment"))
    fingerprint = _require_str(report, "environment_fingerprint_sha256")
    expected_fingerprint = _canonical_sha256(environment)
    if fingerprint != expected_fingerprint:
        raise ValueError(f"{source}: environment fingerprint does not match environment metadata")

    sweep = _require_mapping(report.get("sweep_configuration"), f"{source}: sweep_configuration")
    concurrency_levels = _require_int_list(sweep, "concurrency_levels")
    if not concurrency_levels:
        raise ValueError(f"{source}: concurrency_levels must not be empty")
    if any(level < 1 for level in concurrency_levels):
        raise ValueError(f"{source}: concurrency_levels must be positive")
    if len(set(concurrency_levels)) != len(concurrency_levels):
        raise ValueError(f"{source}: concurrency_levels must be unique")

    raw_points = _require_list(report, "concurrency_envelope")
    points: list[Mapping[str, Any]] = []
    point_levels: list[int] = []
    for index, value in enumerate(raw_points):
        point = dict(_require_mapping(value, f"{source}: concurrency_envelope[{index}]"))
        level = _require_int(point, "concurrency")
        if not _require_bool(point, "correctness_passed"):
            raise ValueError(f"{source}: concurrency envelope contains failed correctness evidence")
        point_levels.append(level)
        points.append(point)
    if tuple(sorted(point_levels)) != tuple(sorted(concurrency_levels)):
        raise ValueError(f"{source}: concurrency envelope does not match declared levels")

    highest = _require_int(report, "highest_verified_concurrency")
    if highest != max(concurrency_levels):
        raise ValueError(f"{source}: highest_verified_concurrency does not match tested levels")

    basis = CrossHostEnvelopeBasis(
        platform_version=_require_str(report, "platform_version"),
        platform_commit=_require_str(report, "platform_commit"),
        deployment_profile=_require_str(report, "deployment_profile"),
        persistence_profile=_require_str(report, "persistence_profile"),
        workload_distribution=_require_str(report, "workload_distribution"),
        operation_count_per_point=_require_int(sweep, "operation_count_per_point"),
        warmup_operations=_require_int(sweep, "warmup_operations"),
        timeout_seconds=_require_number(sweep, "timeout_seconds"),
    )
    return _EnvelopeInput(
        basis=basis,
        environment=environment,
        environment_fingerprint_sha256=fingerprint,
        concurrency_levels=concurrency_levels,
        highest_verified_concurrency=highest,
        longest_verified_endurance_seconds=_require_number(
            report,
            "longest_verified_endurance_seconds",
        ),
        concurrency_envelope=tuple(points),
    )


@lru_cache(maxsize=1)
def _source_envelope_validator() -> Draft202012Validator:
    resource = files("ai_multi_agent_platform.benchmarking").joinpath(
        "schemas/benchmark-operating-envelope.v1.schema.json"
    )
    payload: object = json.loads(resource.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("packaged operating-envelope schema must be a JSON object")
    schema = cast(dict[str, Any], payload)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _validate_source_envelope(report: Mapping[str, Any], *, source: str) -> None:
    error = next(_source_envelope_validator().iter_errors(dict(report)), None)
    if error is None:
        return
    path = ".".join(str(part) for part in error.absolute_path)
    location = f" at {path}" if path else ""
    raise ValueError(f"{source}: invalid operating-envelope report{location}: {error.message}")


def _require_same_basis(
    reference: CrossHostEnvelopeBasis,
    candidate: CrossHostEnvelopeBasis,
    *,
    source: str,
) -> None:
    for field in (
        "platform_version",
        "platform_commit",
        "deployment_profile",
        "persistence_profile",
        "workload_distribution",
        "operation_count_per_point",
        "warmup_operations",
        "timeout_seconds",
    ):
        expected = getattr(reference, field)
        actual = getattr(candidate, field)
        if actual != expected:
            raise ValueError(
                f"{source}: incomparable {field}; expected {expected!r}, got {actual!r}"
            )


def _comparison_status(
    *,
    host_count: int,
    distinct_environment_count: int,
    shared_concurrency_levels: tuple[int, ...],
) -> str:
    if host_count == 1:
        return "single-host"
    if distinct_environment_count == 1:
        return "same-environment-only"
    if not shared_concurrency_levels:
        return "cross-host-no-shared-concurrency"
    return "cross-host-comparable"


def _normalize_labels(labels: Sequence[str], *, count: int) -> tuple[str, ...]:
    normalized = tuple(label.strip() for label in labels)
    if len(normalized) != count:
        raise ValueError("labels must match operating-envelope report count")
    if any(not label for label in normalized):
        raise ValueError("labels must not contain empty values")
    if len(set(normalized)) != len(normalized):
        raise ValueError("labels must be unique")
    return normalized


def _normalize_sources(sources: Sequence[str] | None, *, count: int) -> tuple[str, ...]:
    if sources is None:
        return tuple(f"envelope-{index}.json" for index in range(1, count + 1))
    normalized = tuple(source.strip() for source in sources)
    if len(normalized) != count:
        raise ValueError("sources must match operating-envelope report count")
    if any(not source for source in normalized):
        raise ValueError("sources must not contain empty values")
    return normalized


def _canonical_sha256(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _require_mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    return value


def _require_list(mapping: Mapping[str, Any], field: str) -> list[Any]:
    value = mapping.get(field)
    if not isinstance(value, list):
        raise ValueError(f"{field} must be an array")
    return value


def _require_str(mapping: Mapping[str, Any], field: str) -> str:
    value = mapping.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _require_bool(mapping: Mapping[str, Any], field: str) -> bool:
    value = mapping.get(field)
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean")
    return value


def _require_int(mapping: Mapping[str, Any], field: str) -> int:
    value = mapping.get(field)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    return value


def _require_number(mapping: Mapping[str, Any], field: str) -> float:
    value = mapping.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number")
    return float(value)


def _require_int_list(mapping: Mapping[str, Any], field: str) -> tuple[int, ...]:
    values = _require_list(mapping, field)
    result: list[int] = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{field} must contain integers")
        result.append(value)
    return tuple(result)
