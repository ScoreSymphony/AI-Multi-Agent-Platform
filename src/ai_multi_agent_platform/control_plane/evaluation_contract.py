"""Control Plane resource and command surface for issue #19 evaluation."""

from __future__ import annotations

import json
from typing import cast

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.evaluation.codec import (
    encode_comparison,
    encode_result,
    encode_run,
)
from ai_multi_agent_platform.evaluation.models import (
    ComparisonReport,
    ConfigurationSnapshot,
    EvaluationCase,
    EvaluationResult,
    EvaluationRun,
    EvaluationSuite,
    SnapshotValue,
    VersionReference,
)
from ai_multi_agent_platform.evaluation.service import (
    EvaluationRunDetail,
    EvaluationService,
    evaluation_suite_ref,
    regression_policy_ref,
)

from .extensions import CommandHandler, ResourceService
from .models import PageQuery, RequestContext

EVALUATION_SUITE_COLLECTION = "evaluation-suites"
EVALUATION_RUN_COLLECTION = "evaluation-runs"
EVALUATION_COLLECTIONS = (EVALUATION_SUITE_COLLECTION, EVALUATION_RUN_COLLECTION)
EVALUATION_COMMANDS = ("evaluation.run", "evaluation.compare")


class EvaluationSuiteResourceService(ResourceService):
    """Read configured, versioned evaluation suites."""

    def __init__(self, service: EvaluationService) -> None:
        self._service = service

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del context, query
        return tuple(_suite_resource(suite) for suite in self._service.list_suites())

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        del context
        return _suite_resource(self._service.get_suite(resource_id))


class EvaluationRunResourceService(ResourceService):
    """Read durable evaluation runs with result/comparison detail on single-resource reads."""

    def __init__(self, service: EvaluationService) -> None:
        self._service = service

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del context
        return tuple(
            _run_resource(run) for run in self._service.list_runs(limit=max(query.limit, 100))
        )

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        del context
        return _run_detail_resource(self._service.get_run_detail(resource_id))


def evaluation_resource_services(service: EvaluationService) -> dict[str, ResourceService]:
    """Return canonical read services for explicit Control Plane registration."""

    return {
        EVALUATION_SUITE_COLLECTION: EvaluationSuiteResourceService(service),
        EVALUATION_RUN_COLLECTION: EvaluationRunResourceService(service),
    }


def evaluation_command_handlers(service: EvaluationService) -> dict[str, CommandHandler]:
    """Return canonical mutating commands backed by the Evaluation application service."""

    async def run_suite(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del context
        summary = await service.run_suite(
            suite_ref=resource_ref,
            snapshot=_parse_snapshot(_required_object(payload, "snapshot")),
            repetitions=_optional_positive_int(payload, "repetitions") or 1,
            seed=_optional_int(payload, "seed"),
            baseline_run_id=_optional_string(payload, "baseline_run_id"),
            regression_policy_ref_value=_optional_string(payload, "regression_policy_ref"),
        )
        return _run_detail_resource(service.get_run_detail(summary.run.run_id))

    async def compare(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del context
        comparison = service.compare_runs(
            current_run_id=resource_ref,
            baseline_run_id=_required_string(payload, "baseline_run_id"),
            regression_policy_ref_value=_required_string(payload, "regression_policy_ref"),
        )
        return _comparison_resource(comparison)

    return {
        "evaluation.run": run_suite,
        "evaluation.compare": compare,
    }


def _suite_resource(suite: EvaluationSuite) -> dict[str, JsonValue]:
    return {
        "id": evaluation_suite_ref(suite),
        "type": "evaluation-suite",
        "suite_id": suite.suite_id,
        "version": suite.version,
        "name": suite.name,
        "description": suite.description,
        "tags": list(suite.tags),
        "cases": [_case_resource(case) for case in suite.cases],
    }


def _case_resource(case: EvaluationCase) -> dict[str, JsonValue]:
    return {
        "case_id": case.case_id,
        "version": case.version,
        "name": case.name,
        "category": case.category,
        "difficulty": case.difficulty,
        "tags": list(case.tags),
        "fixtures": list(case.fixtures),
        "assertion_count": len(case.assertions),
        "metric_rule_count": len(case.metric_rules),
        "rubric_criterion_count": len(case.rubric),
    }


def _run_resource(run: EvaluationRun) -> dict[str, JsonValue]:
    resource = _encoded_object(encode_run(run))
    resource["id"] = run.run_id
    resource["type"] = "evaluation-run"
    return resource


def _run_detail_resource(detail: EvaluationRunDetail) -> dict[str, JsonValue]:
    resource = _run_resource(detail.run)
    resource["results"] = [_result_resource(item) for item in detail.results]
    resource["comparison"] = (
        None if detail.comparison is None else _comparison_resource(detail.comparison)
    )
    return resource


def _result_resource(result: EvaluationResult) -> dict[str, JsonValue]:
    resource = _encoded_object(encode_result(result))
    resource["id"] = result.result_id
    resource["type"] = "evaluation-result"
    return resource


def _comparison_resource(comparison: ComparisonReport) -> dict[str, JsonValue]:
    resource = _encoded_object(encode_comparison(comparison))
    resource["id"] = comparison.current_run_id
    resource["type"] = "evaluation-comparison"
    resource["regression_count"] = len(comparison.regressions)
    resource["improvement_count"] = len(comparison.improvements)
    return resource


def _encoded_object(raw: str) -> dict[str, JsonValue]:
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "evaluation codec did not produce a JSON object",
        )
    return cast(dict[str, JsonValue], parsed)


def _parse_snapshot(payload: dict[str, JsonValue]) -> ConfigurationSnapshot:
    raw_references = payload.get("references", [])
    if not isinstance(raw_references, list):
        raise ValueError("snapshot.references must be an array")
    references: list[VersionReference] = []
    for raw_reference in raw_references:
        item = _object(raw_reference, "snapshot.references[]")
        references.append(
            VersionReference(
                kind=_required_string(item, "kind"),
                ref_id=_required_string(item, "ref_id"),
                version=_required_string(item, "version"),
                revision=_optional_string(item, "revision"),
            )
        )

    raw_environment = payload.get("environment", [])
    if not isinstance(raw_environment, list):
        raise ValueError("snapshot.environment must be an array")
    environment: list[SnapshotValue] = []
    for raw_value in raw_environment:
        item = _object(raw_value, "snapshot.environment[]")
        environment.append(
            SnapshotValue(
                key=_required_string(item, "key"),
                value=_required_string(item, "value"),
            )
        )
    return ConfigurationSnapshot(
        platform_version=_required_string(payload, "platform_version"),
        platform_commit=_optional_string(payload, "platform_commit"),
        references=tuple(references),
        environment=tuple(environment),
    )


def _required_object(payload: dict[str, JsonValue], key: str) -> dict[str, JsonValue]:
    return _object(payload.get(key), key)


def _object(value: JsonValue, field: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    return cast(dict[str, JsonValue], value)


def _required_string(payload: dict[str, JsonValue], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-blank string")
    return value


def _optional_string(payload: dict[str, JsonValue], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-blank string or null")
    return value


def _optional_int(payload: dict[str, JsonValue], key: str) -> int | None:
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer or null")
    return value


def _optional_positive_int(payload: dict[str, JsonValue], key: str) -> int | None:
    value = _optional_int(payload, key)
    if value is not None and value <= 0:
        raise ValueError(f"{key} must be greater than zero")
    return value


__all__ = [
    "EVALUATION_COLLECTIONS",
    "EVALUATION_COMMANDS",
    "EVALUATION_RUN_COLLECTION",
    "EVALUATION_SUITE_COLLECTION",
    "EvaluationRunResourceService",
    "EvaluationSuiteResourceService",
    "evaluation_command_handlers",
    "evaluation_resource_services",
    "regression_policy_ref",
]
