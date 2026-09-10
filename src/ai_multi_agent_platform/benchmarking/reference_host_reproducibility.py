"""Same-host reference campaign reproducibility analysis for issue #440."""

from __future__ import annotations

import hashlib
import json
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Any, cast

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

from .operating_envelope_catalog import OperatingEnvelopeCatalogBuilder

REFERENCE_HOST_REPRODUCIBILITY_SCHEMA_VERSION = "1.0"
_CAMPAIGN_SCHEMA_VERSION = "1.0"
_CAMPAIGN_ID = "single-node.reference.host-campaign"
_CAMPAIGN_VERSION = "1.0"
_CAMPAIGN_CLAIM_SEMANTICS = "single-host-tested-evidence-only"
_BUDGET_STATUS = "not-established"
_REPORT_CLAIM_SEMANTICS = "same-host-comparable-campaigns-only"


@dataclass(frozen=True, slots=True)
class ReproducibilityBasis:
    """Dimensions that must match before runs count as a comparable series."""

    host_label: str
    platform_version: str
    platform_commit: str
    profile: str
    configuration_sha256: str
    environment_fingerprint_sha256: str
    deployment_profile: str
    persistence_profile: str
    workload_distribution: str


@dataclass(frozen=True, slots=True)
class CampaignSeriesEvidence:
    """One independently hashed campaign source retained in the series."""

    source: str
    campaign_sha256: str
    operating_envelope_path: str
    operating_envelope_sha256: str
    started_at: str
    completed_at: str


@dataclass(frozen=True, slots=True)
class MetricVariability:
    """Observed variability without applying a stable/unstable policy."""

    sample_count: int
    minimum: float
    median: float
    maximum: float
    relative_range: float | None
    coefficient_of_variation: float | None


@dataclass(frozen=True, slots=True)
class ConcurrencyVariability:
    concurrency: int
    throughput_operations_per_second: MetricVariability
    p95_latency_ms: MetricVariability
    storage_growth_bytes: MetricVariability


@dataclass(frozen=True, slots=True)
class EnduranceVariability:
    throughput_operations_per_second: MetricVariability
    p95_latency_ms: MetricVariability
    traced_memory_growth_bytes: MetricVariability
    peak_rss_growth_bytes: MetricVariability | None
    open_file_descriptor_growth: MetricVariability | None
    storage_growth_bytes: MetricVariability
    latency_drift_ratio: MetricVariability | None


@dataclass(frozen=True, slots=True)
class ReferenceHostReproducibilityReport:
    """Observed variability across comparable campaigns on one host/environment."""

    schema_version: str
    benchmark_id: str
    benchmark_version: str
    generated_at: str
    basis: ReproducibilityBasis
    campaign_count: int
    variability_observed: bool
    comparison_status: str
    claim_semantics: str
    budget_status: str
    stability_classification: str
    campaigns: tuple[CampaignSeriesEvidence, ...]
    concurrency_variability: tuple[ConcurrencyVariability, ...]
    endurance_variability: EnduranceVariability
    correctness_passed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "benchmark_id": self.benchmark_id,
            "benchmark_version": self.benchmark_version,
            "generated_at": self.generated_at,
            "basis": asdict(self.basis),
            "campaign_count": self.campaign_count,
            "variability_observed": self.variability_observed,
            "comparison_status": self.comparison_status,
            "claim_semantics": self.claim_semantics,
            "budget_status": self.budget_status,
            "stability_classification": self.stability_classification,
            "campaigns": [asdict(campaign) for campaign in self.campaigns],
            "concurrency_variability": [
                {
                    "concurrency": point.concurrency,
                    "throughput_operations_per_second": asdict(
                        point.throughput_operations_per_second
                    ),
                    "p95_latency_ms": asdict(point.p95_latency_ms),
                    "storage_growth_bytes": asdict(point.storage_growth_bytes),
                }
                for point in self.concurrency_variability
            ],
            "endurance_variability": {
                "throughput_operations_per_second": asdict(
                    self.endurance_variability.throughput_operations_per_second
                ),
                "p95_latency_ms": asdict(self.endurance_variability.p95_latency_ms),
                "traced_memory_growth_bytes": asdict(
                    self.endurance_variability.traced_memory_growth_bytes
                ),
                "peak_rss_growth_bytes": _optional_metric_dict(
                    self.endurance_variability.peak_rss_growth_bytes
                ),
                "open_file_descriptor_growth": _optional_metric_dict(
                    self.endurance_variability.open_file_descriptor_growth
                ),
                "storage_growth_bytes": asdict(self.endurance_variability.storage_growth_bytes),
                "latency_drift_ratio": _optional_metric_dict(
                    self.endurance_variability.latency_drift_ratio
                ),
            },
            "correctness_passed": self.correctness_passed,
        }


@dataclass(frozen=True, slots=True)
class _CampaignInput:
    source_dir: Path
    campaign: Mapping[str, Any]
    envelope: Mapping[str, Any]
    basis: ReproducibilityBasis
    evidence: CampaignSeriesEvidence


class ReferenceHostReproducibilityAnalyzer:
    """Validate and summarize repeated reference-host campaign evidence."""

    def analyze(
        self,
        *,
        campaign_dirs: Sequence[Path],
    ) -> ReferenceHostReproducibilityReport:
        if not campaign_dirs:
            raise ValueError("at least one reference-host campaign directory is required")

        normalized_dirs = tuple(path.resolve() for path in campaign_dirs)
        if len(set(normalized_dirs)) != len(normalized_dirs):
            raise ValueError("campaign directories must be unique")

        parsed = tuple(_load_campaign(path) for path in normalized_dirs)
        reference = parsed[0]
        seen_campaign_hashes: set[str] = set()
        for item in parsed:
            if item.evidence.campaign_sha256 in seen_campaign_hashes:
                raise ValueError(
                    f"{item.source_dir}: duplicate campaign evidence does not count as a repetition"
                )
            seen_campaign_hashes.add(item.evidence.campaign_sha256)
        for item in parsed[1:]:
            _require_same_basis(reference.basis, item.basis, source=str(item.source_dir))

        levels = _concurrency_levels(reference.envelope, source=str(reference.source_dir))
        concurrency_variability = tuple(_aggregate_concurrency(level, parsed) for level in levels)
        endurance_variability = _aggregate_endurance(parsed)
        campaign_count = len(parsed)
        profile = reference.basis.profile
        if profile == "smoke":
            comparison_status = "smoke-contract-only"
        elif campaign_count == 1:
            comparison_status = "single-release-campaign"
        else:
            comparison_status = "release-variability-observed"

        return ReferenceHostReproducibilityReport(
            schema_version=REFERENCE_HOST_REPRODUCIBILITY_SCHEMA_VERSION,
            benchmark_id="single-node.reference.host-campaign.reproducibility",
            benchmark_version="1.0",
            generated_at=datetime.now(UTC).isoformat(),
            basis=reference.basis,
            campaign_count=campaign_count,
            variability_observed=campaign_count >= 2,
            comparison_status=comparison_status,
            claim_semantics=_REPORT_CLAIM_SEMANTICS,
            budget_status=_BUDGET_STATUS,
            stability_classification="not-performed",
            campaigns=tuple(item.evidence for item in parsed),
            concurrency_variability=concurrency_variability,
            endurance_variability=endurance_variability,
            correctness_passed=True,
        )


def _load_campaign(directory: Path) -> _CampaignInput:
    campaign_path = directory / "campaign.json"
    campaign = _load_json_object(campaign_path)
    _validate_campaign(campaign, source=str(campaign_path))

    configuration = _require_mapping(
        campaign.get("configuration"), f"{campaign_path}: configuration"
    )
    configuration_sha = _require_str(campaign, "configuration_sha256")
    if configuration_sha != _canonical_sha256(configuration):
        raise ValueError(f"{campaign_path}: configuration_sha256 does not match configuration")

    environment = _require_mapping(campaign.get("environment"), f"{campaign_path}: environment")
    environment_fingerprint = _require_str(campaign, "environment_fingerprint_sha256")
    if environment_fingerprint != _canonical_sha256(environment):
        raise ValueError(f"{campaign_path}: environment fingerprint does not match environment")

    envelope_ref = _require_mapping(
        campaign.get("operating_envelope"),
        f"{campaign_path}: operating_envelope",
    )
    envelope_rel = _require_str(envelope_ref, "path")
    envelope_path = _safe_evidence_path(directory, envelope_rel)
    envelope_sha = _require_str(envelope_ref, "sha256")
    if _file_sha256(envelope_path) != envelope_sha:
        raise ValueError(f"{campaign_path}: operating-envelope evidence hash mismatch")

    envelope = _load_json_object(envelope_path)
    host_label = _require_str(campaign, "host_label")
    OperatingEnvelopeCatalogBuilder().build(
        envelopes=(envelope,),
        labels=(host_label,),
        sources=(str(envelope_path),),
    )
    _require_campaign_envelope_match(
        campaign=campaign,
        configuration=configuration,
        envelope=envelope,
        source=str(campaign_path),
    )

    basis = ReproducibilityBasis(
        host_label=host_label,
        platform_version=_require_str(campaign, "platform_version"),
        platform_commit=_require_str(campaign, "platform_commit"),
        profile=_require_str(campaign, "profile"),
        configuration_sha256=configuration_sha,
        environment_fingerprint_sha256=environment_fingerprint,
        deployment_profile=_require_str(envelope, "deployment_profile"),
        persistence_profile=_require_str(envelope, "persistence_profile"),
        workload_distribution=_require_str(envelope, "workload_distribution"),
    )
    evidence = CampaignSeriesEvidence(
        source=str(campaign_path),
        campaign_sha256=_file_sha256(campaign_path),
        operating_envelope_path=str(envelope_path),
        operating_envelope_sha256=envelope_sha,
        started_at=_require_str(campaign, "started_at"),
        completed_at=_require_str(campaign, "completed_at"),
    )
    return _CampaignInput(
        source_dir=directory,
        campaign=campaign,
        envelope=envelope,
        basis=basis,
        evidence=evidence,
    )


@lru_cache(maxsize=1)
def _campaign_validator() -> Draft202012Validator:
    resource = files("ai_multi_agent_platform.benchmarking").joinpath(
        "schemas/benchmark-reference-host-campaign.v1.schema.json"
    )
    payload: object = json.loads(resource.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("packaged reference-host campaign schema must be a JSON object")
    schema = cast(dict[str, Any], payload)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _validate_campaign(campaign: Mapping[str, Any], *, source: str) -> None:
    error = next(_campaign_validator().iter_errors(dict(campaign)), None)
    if error is not None:
        path = ".".join(str(part) for part in error.absolute_path)
        location = f" at {path}" if path else ""
        raise ValueError(f"{source}: invalid reference-host campaign{location}: {error.message}")
    if _require_str(campaign, "schema_version") != _CAMPAIGN_SCHEMA_VERSION:
        raise ValueError(f"{source}: unsupported campaign schema_version")
    if _require_str(campaign, "campaign_id") != _CAMPAIGN_ID:
        raise ValueError(f"{source}: unsupported campaign_id")
    if _require_str(campaign, "campaign_version") != _CAMPAIGN_VERSION:
        raise ValueError(f"{source}: unsupported campaign_version")
    if _require_str(campaign, "claim_semantics") != _CAMPAIGN_CLAIM_SEMANTICS:
        raise ValueError(f"{source}: unsupported campaign claim_semantics")
    if _require_str(campaign, "budget_status") != _BUDGET_STATUS:
        raise ValueError(f"{source}: unsupported campaign budget_status")
    if not _require_bool(campaign, "correctness_passed"):
        raise ValueError(f"{source}: campaign correctness did not pass")


def _require_campaign_envelope_match(
    *,
    campaign: Mapping[str, Any],
    configuration: Mapping[str, Any],
    envelope: Mapping[str, Any],
    source: str,
) -> None:
    for field in ("platform_version", "platform_commit", "environment_fingerprint_sha256"):
        if campaign.get(field) != envelope.get(field):
            raise ValueError(f"{source}: campaign/envelope {field} mismatch")

    sweep = _require_mapping(envelope.get("sweep_configuration"), f"{source}: sweep_configuration")
    expected_pairs = (
        (
            "concurrency_levels",
            configuration.get("concurrency_levels"),
            sweep.get("concurrency_levels"),
        ),
        (
            "operations_per_level",
            configuration.get("operations_per_level"),
            sweep.get("operation_count_per_point"),
        ),
        (
            "warmup_operations",
            configuration.get("warmup_operations"),
            sweep.get("warmup_operations"),
        ),
        (
            "timeout_seconds",
            configuration.get("timeout_seconds"),
            sweep.get("timeout_seconds"),
        ),
    )
    for field, expected, actual in expected_pairs:
        if expected != actual:
            raise ValueError(
                f"{source}: campaign/envelope {field} mismatch; "
                f"expected {expected!r}, got {actual!r}"
            )


def _require_same_basis(
    reference: ReproducibilityBasis,
    candidate: ReproducibilityBasis,
    *,
    source: str,
) -> None:
    for field in (
        "host_label",
        "platform_version",
        "platform_commit",
        "profile",
        "configuration_sha256",
        "environment_fingerprint_sha256",
        "deployment_profile",
        "persistence_profile",
        "workload_distribution",
    ):
        expected = getattr(reference, field)
        actual = getattr(candidate, field)
        if actual != expected:
            raise ValueError(
                f"{source}: incomparable {field}; expected {expected!r}, got {actual!r}"
            )


def _aggregate_concurrency(
    concurrency: int,
    campaigns: Sequence[_CampaignInput],
) -> ConcurrencyVariability:
    points = [
        _concurrency_point(item.envelope, concurrency=concurrency, source=str(item.source_dir))
        for item in campaigns
    ]
    return ConcurrencyVariability(
        concurrency=concurrency,
        throughput_operations_per_second=_metric(
            [_require_number(point, "throughput_median_operations_per_second") for point in points]
        ),
        p95_latency_ms=_metric(
            [_require_number(point, "p95_latency_median_ms") for point in points]
        ),
        storage_growth_bytes=_metric(
            [_require_number(point, "storage_growth_median_bytes") for point in points]
        ),
    )


def _aggregate_endurance(campaigns: Sequence[_CampaignInput]) -> EnduranceVariability:
    rows = [_single_endurance(item.envelope, source=str(item.source_dir)) for item in campaigns]
    return EnduranceVariability(
        throughput_operations_per_second=_metric(
            [_require_number(row, "throughput_operations_per_second") for row in rows]
        ),
        p95_latency_ms=_metric([_require_number(row, "p95_latency_ms") for row in rows]),
        traced_memory_growth_bytes=_metric(
            [_require_number(row, "traced_memory_growth_bytes") for row in rows]
        ),
        peak_rss_growth_bytes=_optional_metric([row.get("peak_rss_growth_bytes") for row in rows]),
        open_file_descriptor_growth=_optional_metric(
            [row.get("open_file_descriptor_growth") for row in rows]
        ),
        storage_growth_bytes=_metric(
            [_require_number(row, "storage_growth_bytes") for row in rows]
        ),
        latency_drift_ratio=_optional_metric([row.get("latency_drift_ratio") for row in rows]),
    )


def _metric(values: Sequence[float]) -> MetricVariability:
    if not values:
        raise ValueError("metric variability requires at least one sample")
    samples = [float(value) for value in values]
    minimum = min(samples)
    maximum = max(samples)
    median = float(statistics.median(samples))
    mean = float(statistics.fmean(samples))
    relative_range = None if median == 0 else (maximum - minimum) / abs(median)
    coefficient = (
        None if len(samples) < 2 or mean == 0 else float(statistics.pstdev(samples) / abs(mean))
    )
    return MetricVariability(
        sample_count=len(samples),
        minimum=minimum,
        median=median,
        maximum=maximum,
        relative_range=relative_range,
        coefficient_of_variation=coefficient,
    )


def _optional_metric(values: Sequence[object]) -> MetricVariability | None:
    present: list[float] = []
    for value in values:
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("optional metric values must be numbers or null")
        present.append(float(value))
    return _metric(present) if present else None


def _optional_metric_dict(metric: MetricVariability | None) -> dict[str, Any] | None:
    return asdict(metric) if metric is not None else None


def _concurrency_levels(envelope: Mapping[str, Any], *, source: str) -> tuple[int, ...]:
    sweep = _require_mapping(envelope.get("sweep_configuration"), f"{source}: sweep_configuration")
    raw = sweep.get("concurrency_levels")
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"{source}: concurrency_levels must be a non-empty list")
    levels: list[int] = []
    for value in raw:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{source}: concurrency_levels must contain integers")
        levels.append(value)
    return tuple(levels)


def _concurrency_point(
    envelope: Mapping[str, Any],
    *,
    concurrency: int,
    source: str,
) -> Mapping[str, Any]:
    raw = envelope.get("concurrency_envelope")
    if not isinstance(raw, list):
        raise ValueError(f"{source}: concurrency_envelope must be a list")
    for value in raw:
        point = _require_mapping(value, f"{source}: concurrency_envelope point")
        if point.get("concurrency") == concurrency:
            return point
    raise ValueError(f"{source}: missing concurrency evidence for {concurrency}")


def _single_endurance(envelope: Mapping[str, Any], *, source: str) -> Mapping[str, Any]:
    raw = envelope.get("endurance_evidence")
    if not isinstance(raw, list) or len(raw) != 1:
        raise ValueError(
            f"{source}: reference-host campaign must contain exactly one endurance sample"
        )
    return _require_mapping(raw[0], f"{source}: endurance_evidence[0]")


def _safe_evidence_path(directory: Path, relative: str) -> Path:
    raw = Path(relative)
    if raw.is_absolute():
        raise ValueError(f"{directory}: evidence paths must be relative")
    root = directory.resolve()
    candidate = (root / raw).resolve()
    if candidate == root or root not in candidate.parents:
        raise ValueError(f"{directory}: evidence path escapes campaign directory")
    return candidate


def _load_json_object(path: Path) -> Mapping[str, Any]:
    payload: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: JSON evidence must be an object")
    return cast(Mapping[str, Any], payload)


def _canonical_sha256(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _require_mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    return cast(Mapping[str, Any], value)


def _require_str(mapping: Mapping[str, Any], field: str) -> str:
    value = mapping.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _require_bool(mapping: Mapping[str, Any], field: str) -> bool:
    value = mapping.get(field)
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean")
    return value


def _require_number(mapping: Mapping[str, Any], field: str) -> float:
    value = mapping.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number")
    return float(value)
