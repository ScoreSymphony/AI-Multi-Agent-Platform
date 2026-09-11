"""Validation and decision-readiness helpers for optional inference backend evaluations."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from importlib.resources import files
from typing import Any, cast

from jsonschema import Draft202012Validator, FormatChecker  # type: ignore[import-untyped]

INFERENCE_BACKEND_EVALUATION_REPORT_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True, slots=True)
class InferenceBackendEvaluationReadiness:
    """Decision-readiness summary derived only from checked-in campaign evidence."""

    ready_for_decision: bool
    report_count: int
    backends_seen: tuple[str, ...]
    missing_contract_cases: tuple[str, ...]
    failed_contract_cases: tuple[str, ...]
    missing_failure_cases: tuple[str, ...]
    failed_failure_cases: tuple[str, ...]
    missing_placement_cases: tuple[str, ...]
    comparable_sglang_vllm_pairs: int
    blockers: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ready_for_decision": self.ready_for_decision,
            "report_count": self.report_count,
            "backends_seen": list(self.backends_seen),
            "missing_contract_cases": list(self.missing_contract_cases),
            "failed_contract_cases": list(self.failed_contract_cases),
            "missing_failure_cases": list(self.missing_failure_cases),
            "failed_failure_cases": list(self.failed_failure_cases),
            "missing_placement_cases": list(self.missing_placement_cases),
            "comparable_sglang_vllm_pairs": self.comparable_sglang_vllm_pairs,
            "blockers": list(self.blockers),
        }


@lru_cache(maxsize=1)
def _report_validator() -> Draft202012Validator:
    schema_path = files("ai_multi_agent_platform.benchmarking").joinpath(
        "schemas",
        "inference-backend-evaluation-report.v1.schema.json",
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def validate_inference_backend_evaluation_report(report: Mapping[str, Any]) -> None:
    """Validate one measured report against the packaged v1 evidence schema."""

    errors = sorted(
        _report_validator().iter_errors(dict(report)),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if not errors:
        return
    first = errors[0]
    location = ".".join(str(part) for part in first.absolute_path) or "<root>"
    raise ValueError(f"invalid inference backend evaluation report at {location}: {first.message}")


def assess_inference_backend_evaluation(
    *,
    campaign: Mapping[str, Any],
    reports: Sequence[Mapping[str, Any]],
) -> InferenceBackendEvaluationReadiness:
    """Assess whether live evidence is sufficient to make the #860 policy decision."""

    campaign_id = _require_str(campaign, "campaign_id")
    required_contract_cases = _require_string_set(campaign, "contract_cases")
    required_failure_cases = _require_string_set(campaign, "failure_cases")
    required_placement_cases = _require_string_set(campaign, "placement_cases")

    normalized_reports: list[Mapping[str, Any]] = []
    for report in reports:
        validate_inference_backend_evaluation_report(report)
        if _require_str(report, "campaign_id") != campaign_id:
            raise ValueError(
                f"report campaign_id {_require_str(report, 'campaign_id')!r} "
                f"does not match {campaign_id!r}"
            )
        normalized_reports.append(report)

    sglang_reports = tuple(
        report for report in normalized_reports if _require_str(report, "backend") == "sglang"
    )
    latest_contract = _latest_case_statuses(sglang_reports, "contract_results")
    latest_failure = _latest_case_statuses(sglang_reports, "failure_results")
    latest_placement = _latest_placement_statuses(sglang_reports)

    missing_contract = tuple(
        sorted(
            case
            for case in required_contract_cases
            if latest_contract.get(case) in {None, "not_measured"}
        )
    )
    failed_contract = tuple(
        sorted(case for case in required_contract_cases if latest_contract.get(case) == "fail")
    )
    missing_failure = tuple(
        sorted(
            case
            for case in required_failure_cases
            if latest_failure.get(case) in {None, "not_measured"}
        )
    )
    failed_failure = tuple(
        sorted(case for case in required_failure_cases if latest_failure.get(case) == "fail")
    )
    missing_placement = tuple(
        sorted(
            case
            for case in required_placement_cases
            if latest_placement.get(case) in {None, "not_measured"}
        )
    )

    comparable_pairs = _count_comparable_sglang_vllm_pairs(normalized_reports)

    blockers: list[str] = []
    if not sglang_reports:
        blockers.append("no measured SGLang report")
    if missing_contract:
        blockers.append("mandatory SGLang contract cases are missing")
    if failed_contract:
        blockers.append("mandatory SGLang contract cases are failing")
    if missing_failure:
        blockers.append("mandatory SGLang failure/recovery cases are missing")
    if failed_failure:
        blockers.append("mandatory SGLang failure/recovery cases are failing")
    if comparable_pairs == 0:
        blockers.append("no comparable decision-eligible SGLang-vLLM performance pair")

    return InferenceBackendEvaluationReadiness(
        ready_for_decision=not blockers,
        report_count=len(normalized_reports),
        backends_seen=tuple(
            sorted({_require_str(report, "backend") for report in normalized_reports})
        ),
        missing_contract_cases=missing_contract,
        failed_contract_cases=failed_contract,
        missing_failure_cases=missing_failure,
        failed_failure_cases=failed_failure,
        missing_placement_cases=missing_placement,
        comparable_sglang_vllm_pairs=comparable_pairs,
        blockers=tuple(blockers),
    )


def _latest_case_statuses(
    reports: Sequence[Mapping[str, Any]],
    field: str,
) -> dict[str, str]:
    latest: dict[str, tuple[datetime, str]] = {}
    for report in reports:
        completed_at = _completed_at(report)
        results = _require_sequence(report.get(field), field)
        for item in results:
            result = _require_mapping(item, field)
            case_id = _require_str(result, "case_id")
            status = _require_str(result, "status")
            previous = latest.get(case_id)
            if previous is None or completed_at >= previous[0]:
                latest[case_id] = (completed_at, status)
    return {case_id: value[1] for case_id, value in latest.items()}


def _latest_placement_statuses(reports: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    latest: dict[str, tuple[datetime, str]] = {}
    for report in reports:
        completed_at = _completed_at(report)
        placement = _require_mapping(report.get("placement"), "placement")
        case_id = _require_str(placement, "case_id")
        status = _require_str(placement, "status")
        previous = latest.get(case_id)
        if previous is None or completed_at >= previous[0]:
            latest[case_id] = (completed_at, status)
    return {case_id: value[1] for case_id, value in latest.items()}


def _completed_at(report: Mapping[str, Any]) -> datetime:
    value = _require_str(report, "completed_at")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"completed_at is not a valid ISO-8601 timestamp: {value!r}") from exc


def _count_comparable_sglang_vllm_pairs(reports: Sequence[Mapping[str, Any]]) -> int:
    sglang = [report for report in reports if _require_str(report, "backend") == "sglang"]
    vllm = [report for report in reports if _require_str(report, "backend") == "vllm"]
    return sum(
        1
        for candidate in sglang
        for comparator in vllm
        if _report_is_decision_eligible(candidate)
        and _report_is_decision_eligible(comparator)
        and _comparison_key(candidate) == _comparison_key(comparator)
    )


def _report_is_decision_eligible(report: Mapping[str, Any]) -> bool:
    if report.get("decision_eligible") is not True:
        return False
    comparability = _require_mapping(report.get("comparability"), "comparability")
    if comparability.get("comparable") is not True:
        return False
    placement = _require_mapping(report.get("placement"), "placement")
    return placement.get("status") == "pass"


def _comparison_key(report: Mapping[str, Any]) -> tuple[Any, ...]:
    model = _require_mapping(report.get("model"), "model")
    environment = _require_mapping(report.get("environment"), "environment")
    workload = _require_mapping(report.get("workload"), "workload")
    worker_ids = tuple(sorted(cast(Sequence[str], environment["worker_ids"])))
    return (
        report["platform_commit"],
        model["model_id"],
        model["model_revision"],
        model["quantization_or_dtype"],
        worker_ids,
        environment["os_kernel"],
        environment["accelerator_runtime"],
        environment["driver_version"],
        environment["gpu_model"],
        environment["gpu_count"],
        environment["gpu_vram_bytes"],
        environment["cpu_model"],
        environment["host_ram_bytes"],
        environment["network_topology"],
        environment["cache_state"],
        workload["scenario_id"],
        workload["request_count"],
        workload["warmup_policy"],
        workload["input_length_policy"],
        workload["output_length_policy"],
        workload["request_rate"],
        workload["concurrency"],
    )


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


def _require_string_set(mapping: Mapping[str, Any], field: str) -> set[str]:
    values = _require_sequence(mapping.get(field), field)
    result: set[str] = set()
    for value in values:
        if not isinstance(value, str) or not value:
            raise ValueError(f"{field} must contain only non-empty strings")
        result.add(value)
    return result
