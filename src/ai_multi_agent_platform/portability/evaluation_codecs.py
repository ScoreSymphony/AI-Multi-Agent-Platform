"""Portable codec for exact versioned EvaluationSuite assets from issue #19."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import cast

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.evaluation.config import parse_evaluation_suite
from ai_multi_agent_platform.evaluation.models import EvaluationCase, EvaluationSuite
from ai_multi_agent_platform.evaluation.product import parse_agent_evaluation_target
from ai_multi_agent_platform.evaluation.service import evaluation_suite_ref
from ai_multi_agent_platform.evaluation.suite_assets import suite_payload

from .dependencies import resource_dependency
from .models import DependencyKind, DependencyRequirement, IdPolicy, PortableResource
from .registry import ImportContext, ResourceExport, ResourceSerializerRegistry

EVALUATION_SUITE_PORTABLE_SCHEMA_VERSION = "1"
EVALUATION_SUITE_RESOURCE_TYPE = "evaluation_suite"
EVALUATION_FIXTURE_RESOURCE_TYPE = "evaluation_fixture"


class EvaluationSuitePortableCodec:
    """Serialize one exact Suite version while keeping Evaluation as the owning domain."""

    resource_type = EVALUATION_SUITE_RESOURCE_TYPE

    def serialize(self, value: object) -> ResourceExport:
        if not isinstance(value, EvaluationSuite):
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "EvaluationSuite portable codec requires a canonical EvaluationSuite",
            )
        return ResourceExport(
            resource_id=evaluation_suite_ref(value),
            resource_version=EVALUATION_SUITE_PORTABLE_SCHEMA_VERSION,
            payload={
                "schema_version": EVALUATION_SUITE_PORTABLE_SCHEMA_VERSION,
                "suite": suite_payload(value),
            },
            id_policy=IdPolicy.PRESERVE,
            dependencies=_suite_dependencies(value),
        )

    def deserialize(self, resource: PortableResource, context: ImportContext) -> object:
        if resource.resource_type != self.resource_type:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                f"EvaluationSuite codec cannot deserialize resource type {resource.resource_type!r}",
            )
        if resource.id_policy is not IdPolicy.PRESERVE:
            raise ContractError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                "EvaluationSuite portability requires preserve identity policy",
            )
        if resource.resource_version != EVALUATION_SUITE_PORTABLE_SCHEMA_VERSION:
            raise ContractError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                "unsupported portable EvaluationSuite schema version",
                details={"supported_schema_version": EVALUATION_SUITE_PORTABLE_SCHEMA_VERSION},
            )
        if resource.payload.get("schema_version") != EVALUATION_SUITE_PORTABLE_SCHEMA_VERSION:
            raise ContractError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                "unsupported EvaluationSuite payload schema version",
                details={"supported_schema_version": EVALUATION_SUITE_PORTABLE_SCHEMA_VERSION},
            )
        raw_suite = resource.payload.get("suite")
        if not isinstance(raw_suite, Mapping):
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "portable EvaluationSuite payload must contain a suite object",
            )
        try:
            suite = parse_evaluation_suite(cast(Mapping[str, object], raw_suite))
            if evaluation_suite_ref(suite) != resource.resource_id:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "portable EvaluationSuite identity disagrees with resource ID",
                )
            return _remap_suite(suite, context)
        except ContractError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "invalid portable EvaluationSuite payload",
                details={"resource_id": resource.resource_id},
            ) from exc


def register_evaluation_suite_portability_codec(registry: ResourceSerializerRegistry) -> None:
    registry.register(EvaluationSuitePortableCodec())


def _suite_dependencies(suite: EvaluationSuite) -> tuple[DependencyRequirement, ...]:
    dependencies: set[DependencyRequirement] = set()
    for case in suite.cases:
        for fixture_id in case.fixtures:
            dependencies.add(
                resource_dependency(
                    EVALUATION_FIXTURE_RESOURCE_TYPE,
                    fixture_id,
                    purpose="EvaluationSuite fixture",
                )
            )
        target = parse_agent_evaluation_target(case)
        if target is None:
            continue
        dependencies.add(
            resource_dependency(
                "agent",
                target.agent_id,
                purpose="EvaluationSuite Agent target",
            )
        )
        if target.model_config_id is not None:
            dependencies.add(
                DependencyRequirement(
                    kind=DependencyKind.MODEL,
                    identifier=target.model_config_id,
                    purpose="EvaluationSuite model target",
                )
            )
        for capability_id in target.capability_ids:
            dependencies.add(
                DependencyRequirement(
                    kind=DependencyKind.CAPABILITY,
                    identifier=capability_id,
                    purpose="EvaluationSuite capability target",
                )
            )
    return tuple(
        sorted(
            dependencies,
            key=lambda item: (
                item.kind.value,
                item.identifier,
                item.required,
                item.version_constraint or "",
                item.purpose or "",
            ),
        )
    )


def _remap_suite(suite: EvaluationSuite, context: ImportContext) -> EvaluationSuite:
    cases = tuple(_remap_case(case, context) for case in suite.cases)
    return replace(suite, cases=cases)


def _remap_case(case: EvaluationCase, context: ImportContext) -> EvaluationCase:
    input_template = _copy_json_object(case.input_template)
    target = input_template.get("evaluation_target")
    if isinstance(target, dict) and target.get("kind") == "agent":
        agent_id = target.get("agent_id")
        if isinstance(agent_id, str):
            target["agent_id"] = context.remap("agent", agent_id)
    fixtures = tuple(
        context.remap(EVALUATION_FIXTURE_RESOURCE_TYPE, fixture_id) for fixture_id in case.fixtures
    )
    return replace(case, input_template=input_template, fixtures=fixtures)


def _copy_json_object(value: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    copied = _copy_json(value)
    if not isinstance(copied, dict):
        raise TypeError("EvaluationCase input_template must remain a JSON object")
    return copied


def _copy_json(value: JsonValue) -> JsonValue:
    if isinstance(value, dict):
        return {key: _copy_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_copy_json(item) for item in value]
    return value


__all__ = [
    "EVALUATION_FIXTURE_RESOURCE_TYPE",
    "EVALUATION_SUITE_PORTABLE_SCHEMA_VERSION",
    "EVALUATION_SUITE_RESOURCE_TYPE",
    "EvaluationSuitePortableCodec",
    "register_evaluation_suite_portability_codec",
]
