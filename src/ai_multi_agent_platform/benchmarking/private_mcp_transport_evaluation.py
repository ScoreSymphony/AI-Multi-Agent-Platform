"""Validation and decision helpers for private remote MCP transport evaluations.

The evidence contract is provider-neutral. Overlay identity is transport evidence only and never
becomes canonical platform authorization or capability policy.
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

NETWORK_EXPOSURE_CASES = frozenset(
    {
        "backend_no_public_listener",
        "unauthorized_external_direct_invocation_unreachable",
        "approved_overlay_invocation_reachable",
        "overlay_control_plane_ports_accounted",
        "backend_protocol_behavior_preserved",
    }
)
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
        "generic_provider_escape_blocked",
        "gateway_filter_cannot_widen",
        "tool_collision_deterministic",
        "dynamic_backend_no_automatic_grant",
    }
)
SECRET_CASES = frozenset(
    {
        "credential_material_absent_from_canonical_state_logs",
        "credential_rotation_exercised",
        "credential_revocation_exercised",
        "restart_persistence_assessed",
        "process_delivery_surface_assessed",
    }
)
FAILURE_RECOVERY_CASES = frozenset(
    {
        "gateway_restart",
        "backend_restart",
        "client_restart",
        "network_partition_reconnect",
        "established_session_revocation_fails_closed",
        "service_removal_reconfiguration_fails_closed",
        "node_b_unavailable_before_invocation",
        "node_b_lost_during_invocation",
        "gateway_reachable_backend_unavailable",
        "malformed_backend_response",
        "overlay_dependency_unavailable",
        "no_duplicate_side_effect_after_retry",
    }
)
LATERAL_MOVEMENT_CASES = frozenset(
    {
        "unrelated_node_b_services_unreachable",
        "gateway_not_unrestricted_tunnel",
        "remote_content_cannot_change_transport_policy",
        "provider_management_separate_from_agent_path",
    }
)
WORKLOAD_CASES = frozenset(
    {
        "small_response_behavior",
        "moderate_structured_response_behavior",
        "streaming_or_long_running_behavior",
    }
)
COST_CASES = frozenset(
    {
        "self_hosted_no_new_recurring_paid_service",
        "local_mcp_without_overlay",
    }
)
COMPARISON_CASES = frozenset(
    {
        "public_remote_mcp_baseline_recorded",
        "simpler_private_route_baseline_recorded",
        "operational_complexity_compared",
        "latency_overhead_compared",
        "security_lateral_movement_compared",
        "secret_delivery_surface_compared",
        "incremental_cost_compared",
        "resource_overhead_measured",
    }
)

_RESULT_FIELDS = {
    "network_exposure_results": NETWORK_EXPOSURE_CASES,
    "identity_authorization_results": IDENTITY_AUTHORIZATION_CASES,
    "capability_results": CAPABILITY_CASES,
    "secret_results": SECRET_CASES,
    "failure_recovery_results": FAILURE_RECOVERY_CASES,
    "lateral_movement_results": LATERAL_MOVEMENT_CASES,
    "workload_results": WORKLOAD_CASES,
    "cost_results": COST_CASES,
    "comparison_results": COMPARISON_CASES,
}
_LATENCY_DISTRIBUTION_FIELDS = (
    "direct_local",
    "private_overlay_warm",
    "small_response",
    "moderate_structured_response",
    "connection_establishment",
    "reconnect",
    "simpler_private_warm",
)

_HARD_RESULT_FIELDS = frozenset(
    {
        "network_exposure_results",
        "identity_authorization_results",
        "capability_results",
        "secret_results",
        "lateral_movement_results",
        "cost_results",
    }
)
_HARD_FAILURE_CASES = frozenset(
    {
        "failure_recovery_results:established_session_revocation_fails_closed",
        "failure_recovery_results:service_removal_reconfiguration_fails_closed",
        "failure_recovery_results:no_duplicate_side_effect_after_retry",
    }
)
_NEGATIVE_ONLY_RECOMMENDATIONS = frozenset({"prefer_simpler_private_networking", "reject/defer"})


@dataclass(frozen=True, slots=True)
class PrivateMCPTransportReadiness:
    """Decision readiness and adoption eligibility from one retained evaluation report."""

    decision_ready: bool
    adoption_eligible: bool
    definition_of_done: bool
    missing_cases: tuple[str, ...]
    failed_cases: tuple[str, ...]
    blockers: tuple[str, ...]
    adoption_blockers: tuple[str, ...]
    recommendation: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_ready": self.decision_ready,
            "adoption_eligible": self.adoption_eligible,
            "definition_of_done": self.definition_of_done,
            "missing_cases": list(self.missing_cases),
            "failed_cases": list(self.failed_cases),
            "blockers": list(self.blockers),
            "adoption_blockers": list(self.adoption_blockers),
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
        message = f"invalid private MCP transport evaluation report at {location}: {first.message}"
        raise ValueError(message)

    started_at = _parse_timestamp(_require_str(report, "started_at"), "started_at")
    completed_at = _parse_timestamp(_require_str(report, "completed_at"), "completed_at")
    if completed_at < started_at:
        raise ValueError("completed_at must not precede started_at")

    for field in _RESULT_FIELDS:
        _case_statuses(report, field)

    latency = _require_mapping(report.get("latency"), "latency")
    for field in _LATENCY_DISTRIBUTION_FIELDS:
        _validate_distribution(latency, field)

    streaming_supported = latency.get("streaming_supported")
    streaming = latency.get("streaming_or_long_running")
    if streaming_supported is True:
        if not isinstance(streaming, Mapping):
            raise ValueError(
                "latency.streaming_or_long_running must be measured when streaming is supported"
            )
        _validate_distribution(latency, "streaming_or_long_running")
    elif streaming_supported is False and streaming is not None:
        raise ValueError(
            "latency.streaming_or_long_running must be null when streaming is not supported"
        )


def assess_private_mcp_transport_evaluation(
    report: Mapping[str, Any],
    *,
    minimum_latency_samples: int = 5,
) -> PrivateMCPTransportReadiness:
    """Assess evidence completeness separately from whether the candidate is adoptable."""

    if minimum_latency_samples < 2:
        raise ValueError("minimum_latency_samples must be >= 2")
    validate_private_mcp_transport_evaluation_report(report)

    missing: list[str] = []
    failed: list[str] = []
    hard_result_failures: list[str] = []

    for field, required_cases in _RESULT_FIELDS.items():
        statuses = _case_statuses(report, field)
        for case_id in sorted(required_cases):
            status = statuses.get(case_id)
            qualified = f"{field}:{case_id}"
            if status in {None, "not_measured"}:
                missing.append(qualified)
                continue
            if status == "not_supported":
                if _not_supported_is_acceptable(report, field, case_id):
                    continue
                failed.append(qualified)
            elif status != "pass":
                failed.append(qualified)

            if qualified in failed and (
                field in _HARD_RESULT_FIELDS or qualified in _HARD_FAILURE_CASES
            ):
                hard_result_failures.append(qualified)

    decision_blockers: list[str] = []
    if missing:
        decision_blockers.append("mandatory evaluation cases are missing")

    comparison_statuses = _case_statuses(report, "comparison_results")
    incomplete_comparisons = sorted(
        case_id for case_id in COMPARISON_CASES if comparison_statuses.get(case_id) != "pass"
    )
    if incomplete_comparisons:
        decision_blockers.append("required comparison baseline evidence is incomplete")

    latency = _require_mapping(report.get("latency"), "latency")
    latency_fields = list(_LATENCY_DISTRIBUTION_FIELDS)
    if latency.get("streaming_supported") is True:
        latency_fields.append("streaming_or_long_running")
    for field in latency_fields:
        distribution = _require_mapping(latency.get(field), f"latency.{field}")
        count = distribution.get("count")
        if isinstance(count, bool) or not isinstance(count, int) or count < minimum_latency_samples:
            decision_blockers.append(
                f"latency.{field} has fewer than {minimum_latency_samples} retained samples"
            )

    adoption_blockers = _adoption_blockers(report, failed)
    decision_ready = not decision_blockers
    adoption_eligible = decision_ready and not adoption_blockers

    completion_blockers = list(decision_blockers)
    recommendation_raw = report.get("recommendation")
    recommendation = recommendation_raw if isinstance(recommendation_raw, str) else None
    if decision_ready and recommendation is None:
        completion_blockers.append("final recommendation is not recorded")
    if decision_ready and report.get("decision_eligible") is not True:
        completion_blockers.append("report is not marked decision_eligible")

    if decision_ready and recommendation == "adopt_reference_private_mcp_profile":
        if not adoption_eligible:
            completion_blockers.append(
                "adopt recommendation conflicts with measured adoption blockers"
            )
    if decision_ready and _has_hard_security_or_cost_blocker(report, hard_result_failures):
        if recommendation is not None and recommendation not in _NEGATIVE_ONLY_RECOMMENDATIONS:
            completion_blockers.append(
                "hard security/cost blockers require a negative final recommendation"
            )

    definition_of_done = decision_ready and not completion_blockers
    return PrivateMCPTransportReadiness(
        decision_ready=decision_ready,
        adoption_eligible=adoption_eligible,
        definition_of_done=definition_of_done,
        missing_cases=tuple(missing),
        failed_cases=tuple(failed),
        blockers=tuple(completion_blockers),
        adoption_blockers=tuple(adoption_blockers),
        recommendation=recommendation,
    )


def _adoption_blockers(
    report: Mapping[str, Any],
    failed_cases: Sequence[str],
) -> list[str]:
    blockers = [f"failed required case: {case}" for case in failed_cases]

    topology = _require_mapping(report.get("topology"), "topology")
    if topology.get("public_backend_exposed") is not False:
        blockers.append("backend MCP service is publicly exposed")
    if topology.get("self_hosted_overlay") is not True:
        blockers.append("self-hosted overlay path is not proven")
    if topology.get("recurring_paid_service_required") is not False:
        blockers.append("evaluated path requires a new recurring paid service")

    authority = _require_mapping(report.get("authority_boundary"), "authority_boundary")
    if authority.get("transport_identity_separate_from_authorization") is not True:
        blockers.append("transport identity is not proven separate from authorization")

    secrets = _require_mapping(report.get("secret_handling"), "secret_handling")
    if secrets.get("secret_references_only") is not True:
        blockers.append("credentials are not confined to secret references")
    if secrets.get("plaintext_secret_leak_detected") is not False:
        blockers.append("plaintext credential material leaked into retained evidence")
    if secrets.get("process_argv_secret_exposure_detected") is not False:
        blockers.append("credential material is exposed through process argv")

    return _deduplicate(blockers)


def _has_hard_security_or_cost_blocker(
    report: Mapping[str, Any],
    hard_result_failures: Sequence[str],
) -> bool:
    if hard_result_failures:
        return True
    topology = _require_mapping(report.get("topology"), "topology")
    authority = _require_mapping(report.get("authority_boundary"), "authority_boundary")
    secrets = _require_mapping(report.get("secret_handling"), "secret_handling")
    return any(
        (
            topology.get("public_backend_exposed") is not False,
            topology.get("self_hosted_overlay") is not True,
            topology.get("recurring_paid_service_required") is not False,
            authority.get("transport_identity_separate_from_authorization") is not True,
            secrets.get("secret_references_only") is not True,
            secrets.get("plaintext_secret_leak_detected") is not False,
            secrets.get("process_argv_secret_exposure_detected") is not False,
        )
    )


def _not_supported_is_acceptable(
    report: Mapping[str, Any],
    field: str,
    case_id: str,
) -> bool:
    if field != "workload_results" or case_id != "streaming_or_long_running_behavior":
        return False
    latency = _require_mapping(report.get("latency"), "latency")
    return latency.get("streaming_supported") is False


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


def _validate_distribution(latency: Mapping[str, Any], field: str) -> None:
    distribution = _require_mapping(latency.get(field), f"latency.{field}")
    p50 = _require_number(distribution, "p50_ms", f"latency.{field}")
    p95 = _require_number(distribution, "p95_ms", f"latency.{field}")
    if p95 < p50:
        raise ValueError(f"latency.{field}.p95_ms must be >= p50_ms")


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


def _deduplicate(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(values))


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
