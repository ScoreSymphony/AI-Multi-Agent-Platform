"""Provider-neutral #502 repository-intelligence evaluation through the #19 framework.

The executor deliberately evaluates the canonical capability boundary rather than a concrete
indexer. The same versioned EvaluationCase can therefore run against the deterministic baseline
and an optional third-party provider while preserving comparable output-schema, provenance,
freshness, latency and payload-size evidence.
"""

from __future__ import annotations

import json
from time import perf_counter_ns

from jsonschema import Draft202012Validator

from ai_multi_agent_platform.capabilities import CapabilityToolProvider
from ai_multi_agent_platform.contracts.types import JsonValue, OperationContext, ToolInvocation
from ai_multi_agent_platform.repository_intelligence.capabilities import (
    RepositoryIntelligenceOperation,
    repository_intelligence_capability_specs,
)
from ai_multi_agent_platform.repository_intelligence.models import RepositoryIntelligenceFreshness

from .context import EvaluationExecutionContext
from .models import EvaluationAttempt, EvaluationCase, EvaluationObservation

_SOURCE_OPERATIONS = frozenset(
    {
        RepositoryIntelligenceOperation.MAP,
        RepositoryIntelligenceOperation.TEXT_SEARCH,
        RepositoryIntelligenceOperation.SOURCE_SLICE,
    }
)
_CURRENT_SOURCE_FRESHNESS = frozenset(
    {
        RepositoryIntelligenceFreshness.LIVE_REVISION.value,
        RepositoryIntelligenceFreshness.WORKSPACE_SNAPSHOT.value,
        RepositoryIntelligenceFreshness.LIVE_WORKSPACE.value,
        RepositoryIntelligenceFreshness.FRESH_INDEX.value,
    }
)
_IMMUTABLE_REVISION_LENGTHS = frozenset({40, 64})


class RepositoryIntelligenceEvaluationCaseExecutor:
    """Execute one #502 capability case against a replaceable provider.

    ``EvaluationCase.input_template`` must contain ``operation`` and ``arguments``. Source-derived
    operations are additionally checked for canonical repository/revision/provider provenance and
    usable freshness. Provider output remains attached to the observation so ordinary deterministic
    #19 assertions can score task-specific correctness without adding a second evaluation model.
    """

    def __init__(self, provider: CapabilityToolProvider) -> None:
        descriptor = provider.descriptor
        if descriptor.provider_type != "repository_intelligence":
            raise ValueError(
                "repository-intelligence evaluation requires provider_type='repository_intelligence'"
            )
        self._provider = provider
        self._provider_id = descriptor.provider_id

    async def execute_case(
        self,
        *,
        case: EvaluationCase,
        attempt: EvaluationAttempt,
        execution_context: EvaluationExecutionContext,
    ) -> EvaluationObservation:
        if execution_context.attempt_id != attempt.attempt_id:
            raise ValueError("evaluation execution context belongs to another attempt")

        operation = _operation(case)
        arguments = _arguments(case)
        invocation = ToolInvocation(
            invocation_id=f"{attempt.attempt_id}:{operation.value}",
            tool_ref=operation.value,
            arguments=arguments,
            context=OperationContext(correlation_id=attempt.attempt_id),
        )

        started_ns = perf_counter_ns()
        result = await self._provider.invoke(invocation)
        elapsed_ns = perf_counter_ns() - started_ns
        output = result.output

        spec = next(
            item
            for item in repository_intelligence_capability_specs()
            if item.capability_id == operation.value
        )
        schema_errors = tuple(
            sorted(
                Draft202012Validator(spec.output_schema or {}).iter_errors(output),
                key=lambda error: tuple(str(part) for part in error.absolute_path),
            )
        )
        contract_errors = [_schema_error(error) for error in schema_errors]
        provenance = _provenance_evidence(
            operation=operation,
            output=output,
            arguments=arguments,
            provider_id=self._provider_id,
        )

        encoded_output = json.dumps(
            output,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        descriptor = self._provider.descriptor
        data: dict[str, JsonValue] = {
            "operation": operation.value,
            "provider_id": self._provider_id,
            "provider_available": descriptor.available,
            "provider_health": descriptor.health.value,
            "contract_valid": not schema_errors,
            "contract_errors": contract_errors,
            "output": output,
            **provenance,
        }
        metrics = {
            "query_latency_ms": elapsed_ns / 1_000_000.0,
            "result_bytes": float(len(encoded_output)),
            "schema_error_count": float(len(schema_errors)),
        }
        return EvaluationObservation(
            data=data,
            metrics=metrics,
            capability_refs=(operation.value,),
        )


def _operation(case: EvaluationCase) -> RepositoryIntelligenceOperation:
    raw = case.input_template.get("operation")
    if not isinstance(raw, str):
        raise ValueError("repository-intelligence evaluation case requires input_template.operation")
    try:
        return RepositoryIntelligenceOperation(raw)
    except ValueError as exc:
        raise ValueError(f"unsupported repository-intelligence evaluation operation: {raw!r}") from exc


def _arguments(case: EvaluationCase) -> dict[str, JsonValue]:
    raw = case.input_template.get("arguments")
    if not isinstance(raw, dict):
        raise ValueError("repository-intelligence evaluation case requires input_template.arguments")
    if any(not isinstance(key, str) for key in raw):
        raise ValueError("repository-intelligence evaluation arguments must use string keys")
    return dict(raw)


def _provenance_evidence(
    *,
    operation: RepositoryIntelligenceOperation,
    output: JsonValue,
    arguments: dict[str, JsonValue],
    provider_id: str,
) -> dict[str, JsonValue]:
    if operation not in _SOURCE_OPERATIONS:
        return {
            "source_provenance_required": False,
            "source_provenance_valid": True,
            "freshness_current": True,
        }

    if not isinstance(output, dict):
        return {
            "source_provenance_required": True,
            "source_provenance_valid": False,
            "freshness_current": False,
        }
    provenance = output.get("provenance")
    if not isinstance(provenance, dict):
        return {
            "source_provenance_required": True,
            "source_provenance_valid": False,
            "freshness_current": False,
        }

    repository_id = arguments.get("repository_id")
    requested_revision = arguments.get("revision", "HEAD")
    resolved_revision = provenance.get("resolved_revision")
    freshness = provenance.get("freshness")
    valid = (
        isinstance(repository_id, str)
        and provenance.get("repository_id") == repository_id
        and isinstance(requested_revision, str)
        and provenance.get("requested_revision") == requested_revision
        and provenance.get("intelligence_provider_id") == provider_id
        and _immutable_revision(resolved_revision)
        and isinstance(freshness, str)
        and freshness in {item.value for item in RepositoryIntelligenceFreshness}
    )
    return {
        "source_provenance_required": True,
        "source_provenance_valid": valid,
        "freshness_current": isinstance(freshness, str) and freshness in _CURRENT_SOURCE_FRESHNESS,
        "resolved_revision": resolved_revision if isinstance(resolved_revision, str) else None,
        "freshness": freshness if isinstance(freshness, str) else None,
    }


def _immutable_revision(value: object) -> bool:
    if not isinstance(value, str) or len(value) not in _IMMUTABLE_REVISION_LENGTHS:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def _schema_error(error: object) -> str:
    path = getattr(error, "absolute_path", ())
    message = getattr(error, "message", "invalid provider output")
    rendered_path = ".".join(str(part) for part in path)
    return f"{rendered_path}: {message}" if rendered_path else str(message)
