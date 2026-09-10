"""Evidence-backed absolute reference-host performance budgets for issue #440."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from functools import cache
from importlib.resources import files
from pathlib import Path
from typing import Any, cast

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

REFERENCE_HOST_ABSOLUTE_BUDGET_SCHEMA_VERSION = "1.0"
_BENCHMARK_ID = "single-node.reference.host-campaign.absolute-budget"
_BENCHMARK_VERSION = "1.0"
_CLAIM_SEMANTICS = "evidence-scoped-absolute-budget-policy-only"
_PLATFORM_BUDGET_STATUS = "not-established"
_BUDGET_SCOPE = "reference-environment-only"
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

_BASIS_FIELDS = (
    "host_label",
    "profile",
    "configuration_sha256",
    "environment_fingerprint_sha256",
    "deployment_profile",
    "persistence_profile",
    "workload_distribution",
)


@dataclass(frozen=True, slots=True)
class AbsoluteBudgetBasis:
    host_label: str
    profile: str
    configuration_sha256: str
    environment_fingerprint_sha256: str
    deployment_profile: str
    persistence_profile: str
    workload_distribution: str


@dataclass(frozen=True, slots=True)
class AbsoluteBudgetRule:
    rule_id: str
    scope: str
    metric: str
    concurrency: int | None
    direction: str
    minimum_metric_sample_count: int
    warning_boundary: float
    release_blocking_boundary: float
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReferenceHostAbsoluteBudgetPolicy:
    source: str
    sha256: str
    policy_id: str
    policy_version: str
    justification: str
    evidence_refs: tuple[str, ...]
    minimum_campaign_count: int
    basis: AbsoluteBudgetBasis
    rules: tuple[AbsoluteBudgetRule, ...]


@dataclass(frozen=True, slots=True)
class AbsoluteBudgetPolicyEvidence:
    source: str
    sha256: str
    policy_id: str
    policy_version: str
    justification: str
    evidence_refs: tuple[str, ...]
    minimum_campaign_count: int


@dataclass(frozen=True, slots=True)
class AbsoluteBudgetSourceEvidence:
    source: str
    sha256: str
    platform_version: str
    platform_commit: str
    campaign_count: int


@dataclass(frozen=True, slots=True)
class MetricObservation:
    sample_count: int
    median: float


@dataclass(frozen=True, slots=True)
class AbsoluteBudgetRuleResult:
    rule_id: str
    scope: str
    metric: str
    concurrency: int | None
    direction: str
    observed_median: float
    sample_count: int
    warning_boundary: float
    release_blocking_boundary: float
    classification: str
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReferenceHostAbsoluteBudgetReport:
    schema_version: str
    benchmark_id: str
    benchmark_version: str
    generated_at: str
    claim_semantics: str
    platform_budget_status: str
    budget_scope: str
    policy: AbsoluteBudgetPolicyEvidence
    evidence: AbsoluteBudgetSourceEvidence
    basis: AbsoluteBudgetBasis
    overall_classification: str
    rules: tuple[AbsoluteBudgetRuleResult, ...]
    correctness_passed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "benchmark_id": self.benchmark_id,
            "benchmark_version": self.benchmark_version,
            "generated_at": self.generated_at,
            "claim_semantics": self.claim_semantics,
            "platform_budget_status": self.platform_budget_status,
            "budget_scope": self.budget_scope,
            "policy": {
                **asdict(self.policy),
                "evidence_refs": list(self.policy.evidence_refs),
            },
            "evidence": asdict(self.evidence),
            "basis": asdict(self.basis),
            "overall_classification": self.overall_classification,
            "rules": [
                {
                    **asdict(rule),
                    "evidence_refs": list(rule.evidence_refs),
                }
                for rule in self.rules
            ],
            "correctness_passed": self.correctness_passed,
        }


class ReferenceHostAbsoluteBudgetEvaluator:
    """Evaluate one comparable release report against an explicit absolute budget policy."""

    def evaluate(
        self,
        *,
        evidence_path: Path,
        policy_path: Path,
    ) -> ReferenceHostAbsoluteBudgetReport:
        evidence_path = evidence_path.resolve()
        policy_path = policy_path.resolve()
        if evidence_path == policy_path:
            raise ValueError("release evidence and absolute budget policy must be distinct files")

        evidence = _load_json_object(evidence_path)
        policy_payload = _load_json_object(policy_path)
        _validate_reproducibility(evidence, source=str(evidence_path))
        policy = _parse_policy(policy_payload, source=policy_path)
        _require_release_evidence(evidence, source=str(evidence_path))
        basis = _require_policy_basis(evidence, policy=policy, source=str(evidence_path))
        _require_campaign_count(evidence, policy=policy, source=str(evidence_path))

        results = tuple(
            _evaluate_rule(rule, evidence=evidence, source=str(evidence_path))
            for rule in policy.rules
        )
        report = ReferenceHostAbsoluteBudgetReport(
            schema_version=REFERENCE_HOST_ABSOLUTE_BUDGET_SCHEMA_VERSION,
            benchmark_id=_BENCHMARK_ID,
            benchmark_version=_BENCHMARK_VERSION,
            generated_at=datetime.now(UTC).isoformat(),
            claim_semantics=_CLAIM_SEMANTICS,
            platform_budget_status=_PLATFORM_BUDGET_STATUS,
            budget_scope=_BUDGET_SCOPE,
            policy=AbsoluteBudgetPolicyEvidence(
                source=policy.source,
                sha256=policy.sha256,
                policy_id=policy.policy_id,
                policy_version=policy.policy_version,
                justification=policy.justification,
                evidence_refs=policy.evidence_refs,
                minimum_campaign_count=policy.minimum_campaign_count,
            ),
            evidence=_source_evidence(evidence_path, evidence),
            basis=basis,
            overall_classification=_overall_classification(results),
            rules=results,
            correctness_passed=True,
        )
        _validate_budget_report(report.to_dict(), source="generated absolute budget report")
        return report


def _parse_policy(
    payload: Mapping[str, Any],
    *,
    source: Path,
) -> ReferenceHostAbsoluteBudgetPolicy:
    _validate_policy(payload, source=str(source))
    evidence_refs = _require_string_tuple(payload, "evidence_refs")
    basis_payload = _require_mapping(payload.get("basis"), f"{source}: basis")
    basis = _parse_basis(basis_payload)
    rule_payloads = _require_mapping_list(payload, "rules")
    rules = tuple(_parse_rule(item, policy_evidence_refs=evidence_refs) for item in rule_payloads)
    rule_ids = tuple(rule.rule_id for rule in rules)
    if len(rule_ids) != len(set(rule_ids)):
        raise ValueError(f"{source}: absolute budget rule_id values must be unique")
    return ReferenceHostAbsoluteBudgetPolicy(
        source=str(source),
        sha256=_file_sha256(source),
        policy_id=_require_str(payload, "policy_id"),
        policy_version=_require_str(payload, "policy_version"),
        justification=_require_str(payload, "justification"),
        evidence_refs=evidence_refs,
        minimum_campaign_count=_require_int(payload, "minimum_campaign_count"),
        basis=basis,
        rules=rules,
    )


def _parse_basis(payload: Mapping[str, Any]) -> AbsoluteBudgetBasis:
    return AbsoluteBudgetBasis(
        host_label=_require_str(payload, "host_label"),
        profile=_require_str(payload, "profile"),
        configuration_sha256=_require_str(payload, "configuration_sha256"),
        environment_fingerprint_sha256=_require_str(payload, "environment_fingerprint_sha256"),
        deployment_profile=_require_str(payload, "deployment_profile"),
        persistence_profile=_require_str(payload, "persistence_profile"),
        workload_distribution=_require_str(payload, "workload_distribution"),
    )


def _parse_rule(
    payload: Mapping[str, Any],
    *,
    policy_evidence_refs: tuple[str, ...],
) -> AbsoluteBudgetRule:
    metric = _require_str(payload, "metric")
    direction = _require_str(payload, "direction")
    expected_direction = _CANONICAL_DIRECTIONS.get(metric)
    if expected_direction is None:
        raise ValueError(f"unsupported absolute budget metric: {metric!r}")
    if direction != expected_direction:
        raise ValueError(
            f"metric {metric!r} must use direction {expected_direction!r}, got {direction!r}"
        )

    warning = _require_finite_number(payload, "warning_boundary")
    blocking = _require_finite_number(payload, "release_blocking_boundary")
    if direction == "higher-is-better":
        if not blocking < warning:
            raise ValueError(
                "higher-is-better absolute budgets require "
                "release_blocking_boundary < warning_boundary"
            )
    elif not warning < blocking:
        raise ValueError(
            "lower-is-better absolute budgets require warning_boundary < release_blocking_boundary"
        )

    evidence_refs = _require_string_tuple(payload, "evidence_refs")
    unknown_refs = set(evidence_refs) - set(policy_evidence_refs)
    if unknown_refs:
        formatted = ", ".join(sorted(unknown_refs))
        raise ValueError(f"rule evidence_refs must be declared by the policy; unknown: {formatted}")

    concurrency_value = payload.get("concurrency")
    concurrency = None
    if concurrency_value is not None:
        if isinstance(concurrency_value, bool) or not isinstance(concurrency_value, int):
            raise ValueError("absolute budget rule concurrency must be an integer or null")
        concurrency = concurrency_value

    return AbsoluteBudgetRule(
        rule_id=_require_str(payload, "rule_id"),
        scope=_require_str(payload, "scope"),
        metric=metric,
        concurrency=concurrency,
        direction=direction,
        minimum_metric_sample_count=_require_int(payload, "minimum_metric_sample_count"),
        warning_boundary=warning,
        release_blocking_boundary=blocking,
        evidence_refs=evidence_refs,
    )


def _require_release_evidence(report: Mapping[str, Any], *, source: str) -> None:
    if _require_str(report, "comparison_status") != _RELEASE_STATUS:
        raise ValueError(
            f"{source}: absolute budget evaluation requires comparison_status={_RELEASE_STATUS!r}"
        )
    if not _require_bool(report, "variability_observed"):
        raise ValueError(f"{source}: absolute budget evidence must contain observed variability")
    if not _require_bool(report, "correctness_passed"):
        raise ValueError(f"{source}: correctness did not pass")
    basis = _require_mapping(report.get("basis"), f"{source}: basis")
    if _require_str(basis, "profile") != "release":
        raise ValueError(f"{source}: only release-profile reproducibility evidence is accepted")


def _require_policy_basis(
    report: Mapping[str, Any],
    *,
    policy: ReferenceHostAbsoluteBudgetPolicy,
    source: str,
) -> AbsoluteBudgetBasis:
    report_basis = _require_mapping(report.get("basis"), f"{source}: basis")
    policy_basis = asdict(policy.basis)
    for field in _BASIS_FIELDS:
        expected = policy_basis[field]
        actual = report_basis.get(field)
        if actual != expected:
            raise ValueError(
                f"{source}: absolute budget policy basis mismatch for {field}; "
                f"expected {expected!r}, got {actual!r}"
            )
    return _parse_basis(report_basis)


def _require_campaign_count(
    report: Mapping[str, Any],
    *,
    policy: ReferenceHostAbsoluteBudgetPolicy,
    source: str,
) -> None:
    campaign_count = _require_int(report, "campaign_count")
    if campaign_count < policy.minimum_campaign_count:
        raise ValueError(
            f"{source}: campaign_count {campaign_count} is below policy minimum "
            f"{policy.minimum_campaign_count}"
        )


def _evaluate_rule(
    rule: AbsoluteBudgetRule,
    *,
    evidence: Mapping[str, Any],
    source: str,
) -> AbsoluteBudgetRuleResult:
    observation = _metric_observation(evidence, rule=rule, source=source)
    if observation.sample_count < rule.minimum_metric_sample_count:
        raise ValueError(
            f"{rule.rule_id}: metric sample_count {observation.sample_count} is below "
            f"policy minimum {rule.minimum_metric_sample_count}"
        )
    return AbsoluteBudgetRuleResult(
        rule_id=rule.rule_id,
        scope=rule.scope,
        metric=rule.metric,
        concurrency=rule.concurrency,
        direction=rule.direction,
        observed_median=observation.median,
        sample_count=observation.sample_count,
        warning_boundary=rule.warning_boundary,
        release_blocking_boundary=rule.release_blocking_boundary,
        classification=_classify(observation.median, rule),
        evidence_refs=rule.evidence_refs,
    )


def _metric_observation(
    report: Mapping[str, Any],
    *,
    rule: AbsoluteBudgetRule,
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
    )


def _classify(value: float, rule: AbsoluteBudgetRule) -> str:
    if rule.direction == "higher-is-better":
        if value <= rule.release_blocking_boundary:
            return "release-blocking"
        if value <= rule.warning_boundary:
            return "warning"
        return "pass"
    if value >= rule.release_blocking_boundary:
        return "release-blocking"
    if value >= rule.warning_boundary:
        return "warning"
    return "pass"


def _overall_classification(results: tuple[AbsoluteBudgetRuleResult, ...]) -> str:
    if any(result.classification == "release-blocking" for result in results):
        return "release-blocking"
    if any(result.classification == "warning" for result in results):
        return "warning"
    return "pass"


def _source_evidence(path: Path, report: Mapping[str, Any]) -> AbsoluteBudgetSourceEvidence:
    basis = _require_mapping(report.get("basis"), f"{path}: basis")
    return AbsoluteBudgetSourceEvidence(
        source=str(path),
        sha256=_file_sha256(path),
        platform_version=_require_str(basis, "platform_version"),
        platform_commit=_require_str(basis, "platform_commit"),
        campaign_count=_require_int(report, "campaign_count"),
    )


@cache
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
        schema_name="benchmark-reference-host-absolute-budget-policy.v1.schema.json",
        source=source,
    )


def _validate_budget_report(payload: Mapping[str, Any], *, source: str) -> None:
    _validate_object(
        payload,
        schema_name="benchmark-reference-host-absolute-budget-report.v1.schema.json",
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
