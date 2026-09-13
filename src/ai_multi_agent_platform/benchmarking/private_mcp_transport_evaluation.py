"""Validation and decision-readiness helpers for private remote MCP transport evaluations.

The evidence contract is provider-neutral. Network/overlay identity remains transport evidence and
never becomes canonical platform authorization or capability policy.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from importlib.resources import files
from typing import Any, cast

from jsonschema import Draft202012Validator, FormatChecker  # type: ignore[import-untyped]

PRIVATE_MCP_TRANSPORT_EVALUATION_REPORT_SCHEMA_VERSION = "1.0"

IDENTITY_AUTHORIZATION_CASES = frozenset(
    {
        "authorized_valid_transport",
        "unauthorized_valid_transport_denied",
        "invalid_or_revoked_transport_fails_closed",
        "cross_client_service_isolation",
        "provider_identifier_not_authority",
    }
)
CAPABILITY_CASES = frozenset(
    {
        "canonical_allowlist_authoritative",
        "gateway_filter_cannot_widen",
        "tool_collision_deterministic",
        "dynamic_backend_no_automatic_grant",
    }
)
FAILURE_RECOVERY_CASES = frozenset(
    {
        "gateway_restart",
        "backend_restart",
        "client_restart",
        "network_partition_reconnect",
        "node_b_unavailable_before_invocation",
        "node_b_lost_during_invocation",
        "no_duplicate_side_effect_after_retry",
    }
)
LATERAL_MOVEMENT_CASES = frozenset(
    {
        "unrelated_node_b_services_unreachable",
        "gateway_not_unrestricted_tunnel",
    }
)
COST_CASES = frozenset(
    {
        "self_hosted_no_new_recurring_paid_service",
        "local_mcp_without_overlay",
    }
)

_RESULT_FIELDS = {
    "identity_authorization_results": IDENTITY_AUTHORIZATION_CASES,
    "capability_results": CAPABILITY_CASES,
    "failure_recovery_results": FAILURE_RECOVERY_CASES,
    "lateral_movement_results": LATERAL_MOVEMENT_CASES,
    "cost_results": COST_CASES,
}
_LATENCY_FIELDS = (
    "direct_local",
    "private_overlay_warm",
    "connection_establishment",
    "reconnect",
)


@dataclass(frozen=True, slots=True)
class PrivateMCPTransportReadiness:
    """Readiness derived only from one validated, retained evaluation report."""

    decision_ready: bool
    definition_of_done: bool
    missing_cases: tuple[str, ...]
    failed_cases: tuple[str, ...]
    blockers: tuple[str, ...]
    recommendation: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_ready": self.decision_ready,
            "definition_of_done": self.definition_of_done,
            "missing_cases": list(self.missing_cases),
            "failed_cases": list(self.failed_cases),
            "blockers": list(self.blockers),
            "recommendation": self.recommendation,
        }


@lru_cache(maxsize=1)
def _report_schema() -> Mapping[str, object]:
    schema_path = files("ai_multi_agent_platform.benchmarking").joinpath(
        "schemas",
        "private-mcp-transport-evaluation-report.v1.schema.json",
    )
    raw: object = json.loads(schema_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("private MCP transport evaluation report schema must be a JSON object")
    schema = cast(dict[str, object], raw)
    Draft202012Validator.check_schema(schema)
    return schema


def validate_private_mcp_transport_evaluation_report(report: Mapping[str, Any]) -> None:
    """Validate one retained private-MCP evaluation report against the packaged v1 schema."""

    _reject_non_finite_numbers(report)
    validator = Draft202012Validator(_report_schema(), format_checker=FormatChecker())
    errors = sorted(
        validator.iter_errors(dict(report)),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        first = errors[0]
        location = ".".join(str(part) for part in first.absolute_path) or "<root>"
        message = (
            "invalid private MCP transport evaluation report at "
            f"{location}: {first.message}"
        )
        raise ValueError(message)

    started_at = _parse_timestamp(_require_str(report, "started_at"), "started_at")
    completed_at = _parse_timestamp(_require_str(report, "completed_at"), "completed_at")
    if completed_at < started_at:
        raise ValueError("completed_at must not precede started_at")

    for field in _RESULT_FIELDS:
        _case_statuses(report, field)

    latency = _require_mapping(report.get("latency"), "latency")
    for field in _LATENCY_FIELDS:
        distribution = _require_mapping(latency.get(field), f"latency.{field}")
        p50 = _require_number(distribution, "p50_ms", f"latency.{field}")
        p95 = _require_number(distribution, "p95_ms", f"latency.{field}")
        if p95 < p50:
            raise ValueError(f"latency.{field}.p95_ms must be >= p50_ms")


def assess_private_mcp_transport_evaluation(
    report: Mapping[str, Any],
    *,
    minimum_latency_samples: int = 5,
) -> PrivateMCPTransportReadiness:
    """Assess #967-style evidence without promoting provider transport state to platform authority."""

    if minimum_latency_samples < 2:
        raise ValueError("minimum_latency_samples must be >= 2")
    validate_private_mcp_transport_evaluation_report(report)

    missing: list[str] = []
    failed: list[str] = []
    for field, required_cases in _RESULT_FIELDS.items():
        statuses = _case_statuses(report, field)
        for case_id in sorted(required_cases):
            status = statuses.get(case_id)
            qualified = f"{field}:{case_id}"
            if status in {None, "not_measured"}:
                missing.append(qualified)
            elif status != "pass":
                failed.append(qualified)

    evidence_blockers: list[str] = []
    if missing:
        evidence_blockers.append("mandatory evaluation cases are missing")
    if failed:
        evidence_blockers.append("mandatory evaluation cases are failing")

    topology = _require_mapping(report.get("topology"), "topology")
    if topology.get("public_backend_exposed") is not False:
        evidence_blockers.append("backend MCP service is publicly exposed")
    if topology.get("self_hosted_overlay") is not True:
        evidence_blockers.append("self-hosted overlay path is not proven")
    if topology.get("recurring_paid_service_required") is not False:
        evidence_blockers.append("evaluated path requires a new recurring paid service")

    authority = _require_mapping(report.get("authority_boundary"), "authority_boundary")
    if authority.get("transport_identity_separate_from_authorization") is not True:
        evidence_blockers.append("transport identity is not proven separate from authorization")

    secrets = _require_mapping(report.get("secret_handling"), "secret_handling")
    if secrets.get("secret_references_only") is not True:
        evidence_blockers.append("credentials are not confined to secret references")
    if secrets.get("plaintext_secret_leak_detected") is not False:
        evidence_blockers.append("plaintext credential material leaked into retained evidence")
    if secrets.get("process_argv_secret_exposure_detected") is not False:
        evidence_blockers.append("credential material is exposed through process argv")

    latency = _require_mapping(report.get("latency"), "latency")
    for field in _LATENCY_FIELDS:
        distribution = _require_mapping(latency.get(field), f"latency.{field}")
        count = distribution.get("count")
        if isinstance(count, bool) or not isinstance(count, int) or count < minimum_latency_samples:
            evidence_blockers.append(
                f"latency.{field} has fewer than {minimum_latency_samples} retained samples"
            )

    decision_ready = not evidence_blockers
    completion_blockers = list(evidence_blockers)
    recommendation_raw = report.get("recommendation")
    recommendation = recommendation_raw if isinstance(recommendation_raw, str) else None
    if decision_ready and recommendation is None:
        completion_blockers.append("final recommendation is not recorded")
    if decision_ready and report.get("decision_eligible") is not True:
        completion_blockers.append("report is not marked decision_eligible")

    definition_of_done = decision_ready and not completion_blockers
    return PrivateMCPTransportReadiness(
        decision_ready=decision_ready,
        definition_of_done=definition_of_done,
        missing_cases=tuple(missing),
        failed_cases=tuple(failed),
        blockers=tuple(completion_blockers),
        recommendation=recommendation,
    )


def _case_statuses(report: Mapping[str, Any], field: str) -> dict[str, str]:
    results = _require_sequence(report.get(field), field)
    statuses: dict[str, str] = {}
    for item in results:
        result = _require_mapping(item, field)
        case_id = _require_str(result, "case_id")
        if case_id in statuses:
            raise ValueError(f"duplicate {field} case_id {case_id!r}")
        statuses[case_id] = _require_str(result, "status")
    return statuses


def _parse_timestamp(value: str, field: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} is not a valid ISO-8601 timestamp: {value!r}") from exc


def _reject_non_finite_numbers(value: object, *, path: str = "<root>") -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"non-finite numeric evidence is not permitted at {path}")
    if isinstance(value, Mapping):
        for key, item in value.items():
            _reject_non_finite_numbers(item, path=f"{path}.{key}")
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            _reject_non_finite_numbers(item, path=f"{path}[{index}]")


def _require_mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    return cast(Mapping[str, Any], value)


def _require_sequence(value: object, field: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError(f"{field} must be an array")
    return cast(Sequence[object], value)


def _require_str(mapping: Mapping[str, Any], field: str) -> str:
    value = mapping.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _require_number(mapping: Mapping[str, Any], field: str, context: str) -> float:
    value = mapping.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{context}.{field} must be numeric")
    return float(value)
