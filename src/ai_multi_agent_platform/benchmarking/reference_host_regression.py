"""Evidence-backed reference-host performance regression comparison for issue #440."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Any, cast

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

REFERENCE_HOST_REGRESSION_SCHEMA_VERSION = "1.0"
_BENCHMARK_ID = "single-node.reference.host-campaign.regression"
_BENCHMARK_VERSION = "1.0"
_CLAIM_SEMANTICS = "comparable-release-regression-policy-only"
_PLATFORM_BUDGET_STATUS = "not-established"
_RELEASE_STATUS = "release-variability-observed"

_CANONICAL_DIRECTIONS = {
    "throughput_operations_per_second": "higher-is-better",
    "p95_latency_ms": "lower-is-better",
    "storage_growth_bytes": "lower-is-better",
    "traced_memory_growth_bytes": "lower-is-better",
    "peak_rss_growth_bytes": "lower-is-better",
    "open_file_descriptor_growth": "lower-is-better",
    "latency_drift_ratio": "lower-is-better",
}

_COMPARABLE_BASIS_FIELDS = (
    "host_label",
    "profile",
    "configuration_sha256",
    "environment_fingerprint_sha256",
    "deployment_profile",
    "persistence_profile",
    "workload_distribution",
)


@dataclass(frozen=True, slots=True)
class RegressionPolicyRule:
    rule_id: str
    scope: str
    metric: str
    concurrency: int | None
    direction: str
    minimum_metric_sample_count: int
    noise_tolerance_relative: float
    warning_relative_regression: float
    release_blocking_relative_regression: float
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReferenceHostRegressionPolicy:
    source: str
    sha256: str
    policy_id: str
    policy_version: str
    justification: str
    evidence_refs: tuple[str, ...]
    minimum_campaign_count: int
    rules: tuple[RegressionPolicyRule, ...]


@dataclass(frozen=True, slots=True)
class RegressionPolicyEvidence:
    source: str
    sha256: str
    policy_id: str
    policy_version: str
    justification: str
    evidence_refs: tuple[str, ...]
    minimum_campaign_count: int


@dataclass(frozen=True, slots=True)
class RegressionSourceEvidence:
    source: str
    sha256: str
    platform_version: str
    platform_commit: str
    campaign_count: int


@dataclass(frozen=True, slots=True)
class RegressionBasis:
    host_label: str
    profile: str
    configuration_sha256: str
    environment_fingerprint_sha256: str
    deployment_profile: str
    persistence_profile: str
    workload_distribution: str


@dataclass(frozen=True, slots=True)
class MetricObservation:
    sample_count: int
    median: float
    relative_range: float | None
    coefficient_of_variation: float | None


@dataclass(frozen=True, slots=True)
class RegressionRuleResult:
    rule_id: str
    scope: str
    metric: str
    concurrency: int | None
    direction: str
    baseline_median: float
    candidate_median: float
    baseline_sample_count: int
    candidate_sample_count: int
    baseline_relative_range: float | None
    baseline_coefficient_of_variation: float | None
    relative_regression: float
    noise_tolerance_relative: float
    warning_relative_regression: float
    release_blocking_relative_regression: float
    classification: str
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReferenceHostRegressionReport:
    schema_version: str
    benchmark_id: str
    benchmark_version: str
    generated_at: str
    claim_semantics: str
    platform_budget_status: str
    policy: RegressionPolicyEvidence
    baseline: RegressionSourceEvidence
    candidate: RegressionSourceEvidence
    basis: RegressionBasis
    overall_classification: str
    rules: tuple[RegressionRuleResult, ...]
    correctness_passed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "benchmark_id": self.benchmark_id,
            "benchmark_version": self.benchmark_version,
            "generated_at": self.generated_at,
            "claim_semantics": self.claim_semantics,
            "platform_budget_status": self.platform_budget_status,
            "policy": asdict(self.policy),
            "baseline": asdict(self.baseline),
            "candidate": asdict(self.candidate),
            "basis": asdict(self.basis),
            "overall_classification": self.overall_classification,
            "rules": [asdict(rule) for rule in self.rules],
            "correctness_passed": self.correctness_passed,
        }


class ReferenceHostRegressionComparator:
    """Classify comparable release evidence under an explicit evidence-backed policy."""

    def compare(
        self,
        *,
        baseline_path: Path,
        candidate_path: Path,
        policy_path: Path,
    ) -> ReferenceHostRegressionReport:
        baseline_path = baseline_path.resolve()
        candidate_path = candidate_path.resolve()
        policy_path = policy_path.resolve()
        if baseline_path == candidate_path:
            raise ValueError("baseline and candidate reproducibility evidence must be distinct files")

        baseline = _load_json_object(baseline_path)
        candidate = _load_json_object(candidate_path)
        policy_payload = _load_json_object(policy_path)

        _validate_reproducibility(baseline, source=str(baseline_path))
        _validate_reproducibility(candidate, source=str(candidate_path))
        policy = _parse_policy(policy_payload, source=policy_path)

        _require_release_evidence(baseline, source=str(baseline_path))
        _require_release_evidence(candidate, source=str(candidate_path))
        basis = _require_comparable_basis(
            baseline,
            candidate,
            baseline_source=str(baseline_path),
            candidate_source=str(candidate_path),
        )
        _require_campaign_count(
            baseline,
            policy=policy,
            source=str(baseline_path),
        )
        _require_campaign_count(
            candidate,
            policy=policy,
            source=str(candidate_path),
        )

        results = tuple(
            _compare_rule(
                rule,
                baseline=baseline,
                candidate=candidate,
                baseline_source=str(baseline_path),
                candidate_source=str(candidate_path),
            )
            for rule in policy.rules
        )
        report = ReferenceHostRegressionReport(
            schema_version=REFERENCE_HOST_REGRESSION_SCHEMA_VERSION,
            benchmark_id=_BENCHMARK_ID,
            benchmark_version=_BENCHMARK_VERSION,
            generated_at=datetime.now(UTC).isoformat(),
            claim_semantics=_CLAIM_SEMANTICS,
            platform_budget_status=_PLATFORM_BUDGET_STATUS,
            policy=RegressionPolicyEvidence(
                source=policy.source,
                sha256=policy.sha256,
                policy_id=policy.policy_id,
                policy_version=policy.policy_version,
                justification=policy.justification,
                evidence_refs=policy.evidence_refs,
                minimum_campaign_count=policy.minimum_campaign_count,
            ),
            baseline=_source_evidence(baseline_path, baseline),
            candidate=_source_evidence(candidate_path, candidate),
            basis=basis,
            overall_classification=_overall_classification(results),
            rules=results,
            correctness_passed=True,
        )
        _validate_regression_report(report.to_dict(), source="generated regression report")
        return report


def _parse_policy(
    payload: Mapping[str, Any],
    *,
    source: Path,
) -> ReferenceHostRegressionPolicy:
    _validate_policy(payload, source=str(source))
    evidence_refs = _require_string_tuple(payload, "evidence_refs")
    minimum_campaign_count = _require_int(payload, "minimum_campaign_count")
    rule_payloads = _require_mapping_list(payload, "rules")
    rules = tuple(_parse_rule(item, policy_evidence_refs=evidence_refs) for item in rule_payloads)
    rule_ids = tuple(rule.rule_id for rule in rules)
    if len(rule_ids) != len(set(rule_ids)):
        raise ValueError(f"{source}: regression policy rule_id values must be unique")
    return ReferenceHostRegressionPolicy(
        source=str(source),
        sha256=_file_sha256(source),
        policy_id=_require_str(payload, "policy_id"),
        policy_version=_require_str(payload, "policy_version"),
        justification=_require_str(payload, "justification"),
        evidence_refs=evidence_refs,
        minimum_campaign_count=minimum_campaign_count,
        rules=rules,
    )


def _parse_rule(
    payload: Mapping[str, Any],
    *,
    policy_evidence_refs: tuple[str, ...],
) -> RegressionPolicyRule:
    metric = _require_str(payload, "metric")
    direction = _require_str(payload, "direction")
    expected_direction = _CANONICAL_DIRECTIONS.get(metric)
    if expected_direction is None:
        raise ValueError(f"unsupported performance regression metric: {metric!r}")
    if direction != expected_direction:
        raise ValueError(
            f"metric {metric!r} must use direction {expected_direction!r}, got {direction!r}"
        )

    noise = _require_finite_number(payload, "noise_tolerance_relative")
    warning = _require_finite_number(payload, "warning_relative_regression")
    blocking = _require_finite_number(payload, "release_blocking_relative_regression")
    if not noise < warning < blocking:
        raise ValueError(
            "regression thresholds must satisfy noise_tolerance_relative < "
            "warning_relative_regression < release_blocking_relative_regression"
        )

    evidence_refs = _require_string_tuple(payload, "evidence_refs")
    unknown_refs = set(evidence_refs) - set(policy_evidence_refs)
    if unknown_refs:
        formatted = ", ".join(sorted(unknown_refs))
        raise ValueError(
            f"rule evidence_refs must be declared by the policy; unknown: {formatted}"
        )

    concurrency_value = payload.get("concurrency")
    concurrency = None
    if concurrency_value is not None:
        if isinstance(concurrency_value, bool) or not isinstance(concurrency_value, int):
            raise ValueError("regression rule concurrency must be an integer or null")
        concurrency = concurrency_value

    return RegressionPolicyRule(
        rule_id=_require_str(payload, "rule_id"),
        scope=_require_str(payload, "scope"),
        metric=metric,
        concurrency=concurrency,
        direction=direction,
        minimum_metric_sample_count=_require_int(payload, "minimum_metric_sample_count"),
        noise_tolerance_relative=noise,
        warning_relative_regression=warning,
        release_blocking_relative_regression=blocking,
        evidence_refs=evidence_refs,
    )


def _require_release_evidence(report: Mapping[str, Any], *, source: str) -> None:
    if _require_str(report, "comparison_status") != _RELEASE_STATUS:
        raise ValueError(
            f"{source}: regression classification requires comparison_status={_RELEASE_STATUS!r}"
        )
    if not _require_bool(report, "variability_observed"):
        raise ValueError(f"{source}: release regression evidence must contain observed variability")
    if not _require_bool(report, "correctness_passed"):
        raise ValueError(f"{source}: correctness did not pass")
    basis = _require_mapping(report.get("basis"), f"{source}: basis")
    if _require_str(basis, "profile") != "release":
        raise ValueError(f"{source}: only release-profile reproducibility evidence is comparable")


def _require_campaign_count(
    report: Mapping[str, Any],
    *,
    policy: ReferenceHostRegressionPolicy,
    source: str,
) -> None:
    campaign_count = _require_int(report, "campaign_count")
    if campaign_count < policy.minimum_campaign_count:
        raise ValueError(
            f"{source}: campaign_count {campaign_count} is below policy minimum "
            f"{policy.minimum_campaign_count}"
        )


def _require_comparable_basis(
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
    *,
    baseline_source: str,
    candidate_source: str,
) -> RegressionBasis:
    baseline_basis = _require_mapping(baseline.get("basis"), f"{baseline_source}: basis")
    candidate_basis = _require_mapping(candidate.get("basis"), f"{candidate_source}: basis")
    for field in _COMPARABLE_BASIS_FIELDS:
        expected = baseline_basis.get(field)
        actual = candidate_basis.get(field)
        if expected != actual:
            raise ValueError(
                f"{candidate_source}: incomparable {field}; expected {expected!r} from "
                f"{baseline_source}, got {actual!r}"
            )
    return RegressionBasis(
        host_label=_require_str(baseline_basis, "host_label"),
        profile=_require_str(baseline_basis, "profile"),
        configuration_sha256=_require_str(baseline_basis, "configuration_sha256"),
        environment_fingerprint_sha256=_require_str(
            baseline_basis,
            "environment_fingerprint_sha256",
        ),
        deployment_profile=_require_str(baseline_basis, "deployment_profile"),
        persistence_profile=_require_str(baseline_basis, "persistence_profile"),
        workload_distribution=_require_str(baseline_basis, "workload_distribution"),
    )


def _compare_rule(
    rule: RegressionPolicyRule,
    *,
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
    baseline_source: str,
    candidate_source: str,
) -> RegressionRuleResult:
    baseline_metric = _metric_observation(baseline, rule=rule, source=baseline_source)
    candidate_metric = _metric_observation(candidate, rule=rule, source=candidate_source)
    for label, metric in (("baseline", baseline_metric), ("candidate", candidate_metric)):
        if metric.sample_count < rule.minimum_metric_sample_count:
            raise ValueError(
                f"{rule.rule_id}: {label} metric sample_count {metric.sample_count} is below "
                f"policy minimum {rule.minimum_metric_sample_count}"
            )
    if baseline_metric.median == 0:
        raise ValueError(
            f"{rule.rule_id}: relative regression is undefined for a zero baseline median"
        )

    if rule.direction == "higher-is-better":
        relative_regression = (
            baseline_metric.median - candidate_metric.median
        ) / abs(baseline_metric.median)
    else:
        relative_regression = (
            candidate_metric.median - baseline_metric.median
        ) / abs(baseline_metric.median)
    classification = _classify(relative_regression, rule)
    return RegressionRuleResult(
        rule_id=rule.rule_id,
        scope=rule.scope,
        metric=rule.metric,
        concurrency=rule.concurrency,
        direction=rule.direction,
        baseline_median=baseline_metric.median,
        candidate_median=candidate_metric.median,
        baseline_sample_count=baseline_metric.sample_count,
        candidate_sample_count=candidate_metric.sample_count,
        baseline_relative_range=baseline_metric.relative_range,
        baseline_coefficient_of_variation=baseline_metric.coefficient_of_variation,
        relative_regression=relative_regression,
        noise_tolerance_relative=rule.noise_tolerance_relative,
        warning_relative_regression=rule.warning_relative_regression,
        release_blocking_relative_regression=rule.release_blocking_relative_regression,
        classification=classification,
        evidence_refs=rule.evidence_refs,
    )


def _metric_observation(
    report: Mapping[str, Any],
    *,
    rule: RegressionPolicyRule,
    source: str,
) -> MetricObservation:
    if rule.scope == "concurrency":
        assert rule.concurrency is not None
        points = _require_mapping_list(report, "concurrency_variability")
        point = next(
            (item for item in points if item.get("concurrency") == rule.concurrency),
            None,
        )
        if point is None:
            raise ValueError(
                f"{source}: no concurrency variability evidence for level {rule.concurrency}"
            )
        metric_payload = point.get(rule.metric)
    else:
        endurance = _require_mapping(report.get("endurance_variability"), f"{source}: endurance")
        metric_payload = endurance.get(rule.metric)

    if metric_payload is None:
        raise ValueError(f"{source}: metric {rule.metric!r} is unavailable for rule {rule.rule_id}")
    metric = _require_mapping(metric_payload, f"{source}: metric {rule.metric}")
    return MetricObservation(
        sample_count=_require_int(metric, "sample_count"),
        median=_require_finite_number(metric, "median"),
        relative_range=_optional_finite_number(metric, "relative_range"),
        coefficient_of_variation=_optional_finite_number(
            metric,
            "coefficient_of_variation",
        ),
    )


def _classify(relative_regression: float, rule: RegressionPolicyRule) -> str:
    if abs(relative_regression) <= rule.noise_tolerance_relative:
        return "within-noise"
    if relative_regression < 0:
        return "improvement"
    if relative_regression >= rule.release_blocking_relative_regression:
        return "release-blocking"
    if relative_regression >= rule.warning_relative_regression:
        return "warning"
    return "pass"


def _overall_classification(results: tuple[RegressionRuleResult, ...]) -> str:
    if any(result.classification == "release-blocking" for result in results):
        return "release-blocking"
    if any(result.classification == "warning" for result in results):
        return "warning"
    return "pass"


def _source_evidence(path: Path, report: Mapping[str, Any]) -> RegressionSourceEvidence:
    basis = _require_mapping(report.get("basis"), f"{path}: basis")
    return RegressionSourceEvidence(
        source=str(path),
        sha256=_file_sha256(path),
        platform_version=_require_str(basis, "platform_version"),
        platform_commit=_require_str(basis, "platform_commit"),
        campaign_count=_require_int(report, "campaign_count"),
    )


@lru_cache(maxsize=None)
def _validator(schema_name: str) -> Draft202012Validator:
    resource = files("ai_multi_agent_platform.benchmarking").joinpath(f"schemas/{schema_name}")
    payload: object = json.loads(resource.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"packaged schema {schema_name!r} must be a JSON object")
    schema = cast(dict[str, Any], payload)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _validate_reproducibility(payload: Mapping[str, Any], *, source: str) -> None:
    _validate_object(
        payload,
        schema_name="benchmark-reference-host-reproducibility.v1.schema.json",
        source=source,
    )


def _validate_policy(payload: Mapping[str, Any], *, source: str) -> None:
    _validate_object(
        payload,
        schema_name="benchmark-reference-host-regression-policy.v1.schema.json",
        source=source,
    )


def _validate_regression_report(payload: Mapping[str, Any], *, source: str) -> None:
    _validate_object(
        payload,
        schema_name="benchmark-reference-host-regression-report.v1.schema.json",
        source=source,
    )


def _validate_object(
    payload: Mapping[str, Any],
    *,
    schema_name: str,
    source: str,
) -> None:
    error = next(_validator(schema_name).iter_errors(dict(payload)), None)
    if error is None:
        return
    path = ".".join(str(part) for part in error.absolute_path)
    location = f" at {path}" if path else ""
    raise ValueError(f"{source}: schema validation failed{location}: {error.message}")


def _load_json_object(path: Path) -> dict[str, Any]:
    payload: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return cast(dict[str, Any], payload)


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _require_mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return cast(Mapping[str, Any], value)


def _require_mapping_list(payload: Mapping[str, Any], field: str) -> tuple[Mapping[str, Any], ...]:
    value = payload.get(field)
    if not isinstance(value, list):
        raise ValueError(f"{field} must be an array")
    rows: list[Mapping[str, Any]] = []
    for index, item in enumerate(value):
        rows.append(_require_mapping(item, f"{field}[{index}]"))
    return tuple(rows)


def _require_str(payload: Mapping[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _require_string_tuple(payload: Mapping[str, Any], field: str) -> tuple[str, ...]:
    value = payload.get(field)
    if not isinstance(value, list) or not value:
        raise ValueError(f"{field} must be a non-empty array")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item:
            raise ValueError(f"{field} must contain only non-empty strings")
        result.append(item)
    return tuple(result)


def _require_bool(payload: Mapping[str, Any], field: str) -> bool:
    value = payload.get(field)
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean")
    return value


def _require_int(payload: Mapping[str, Any], field: str) -> int:
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    return value


def _require_finite_number(payload: Mapping[str, Any], field: str) -> float:
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite")
    return result


def _optional_finite_number(payload: Mapping[str, Any], field: str) -> float | None:
    value = payload.get(field)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number or null")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite when present")
    return result
