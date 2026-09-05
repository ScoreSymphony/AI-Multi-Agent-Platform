"""Completion hardening helpers for canonical evaluation execution.

This module keeps issue #19 completion rules additive: non-output behavior becomes
assertable without duplicating it into executor-private data, resource limits receive
explicit deterministic semantics, and configuration snapshots can be enriched and
validated without weakening their canonical identity.
"""

from __future__ import annotations

from dataclasses import replace
from math import isfinite

from ai_multi_agent_platform.contracts.types import JsonValue

from .models import (
    ComparisonOperator,
    ConfigurationSnapshot,
    EvaluationCase,
    EvaluationObservation,
    EvaluationOutcome,
    EvaluationResult,
    EvaluatorDescriptor,
    EvaluatorKind,
    MetricResult,
    VersionReference,
)

_BEHAVIOR_KEY = "behavior"


def observation_assertion_payload(observation: EvaluationObservation) -> dict[str, JsonValue]:
    """Expose canonical non-output observation fields to deterministic assertions.

    Executor-produced ``data`` remains untouched. Platform-owned behavior metadata is
    available below the reserved ``behavior`` namespace so cases can assert model,
    provider, capability, event and provenance behavior without a second evaluator API.
    """

    if _BEHAVIOR_KEY in observation.data:
        raise ValueError("evaluation observation data must not shadow reserved behavior metadata")
    payload = dict(observation.data)
    behavior: dict[str, JsonValue] = {
        "task_id": observation.task_id,
        "run_id": observation.run_id,
        "artifact_refs": list(observation.artifact_refs),
        "telemetry_refs": list(observation.telemetry_refs),
        "selected_model_config_id": observation.selected_model_config_id,
        "selected_provider_id": observation.selected_provider_id,
        "capability_refs": list(observation.capability_refs),
        "event_types": list(observation.event_types),
    }
    payload[_BEHAVIOR_KEY] = behavior
    return payload


class ResourceLimitEvaluator:
    """Deterministically enforce case-declared maximum observed resource metrics.

    ``EvaluationCase.resource_limits`` retains its existing compact ``SnapshotValue``
    representation. Each entry is interpreted as ``metric_name -> numeric maximum`` and
    therefore evaluates as ``observed_metric <= configured_limit``. A required metric
    that is absent fails the resource-limit result rather than being fabricated.

    These are evaluation acceptance limits over canonical measured/reported metrics;
    they are not an OS sandbox or executor cgroup implementation.
    """

    descriptor = EvaluatorDescriptor(
        evaluator_id="reference.resource-limit",
        kind=EvaluatorKind.METRIC,
        version="1.0",
        deterministic=True,
    )

    def evaluate(
        self,
        *,
        evaluation_run_id: str,
        case: EvaluationCase,
        observation: EvaluationObservation,
    ) -> EvaluationResult:
        results: list[MetricResult] = []
        passed = True
        seen: set[str] = set()
        for limit in case.resource_limits:
            metric_name = limit.key.strip()
            if metric_name in seen:
                raise ValueError(f"duplicate evaluation resource limit metric: {metric_name}")
            seen.add(metric_name)
            try:
                maximum = float(limit.value)
            except ValueError as exc:
                raise ValueError(
                    f"evaluation resource limit {metric_name!r} must be numeric"
                ) from exc
            if not isfinite(maximum) or maximum < 0:
                raise ValueError(
                    f"evaluation resource limit {metric_name!r} must be finite and >= 0"
                )
            observed = observation.metrics.get(metric_name)
            if observed is None:
                passed = False
                continue
            metric_passed = observed <= maximum
            passed = passed and metric_passed
            results.append(
                MetricResult(
                    metric_name=metric_name,
                    value=observed,
                    passed=metric_passed,
                    threshold=maximum,
                    operator=ComparisonOperator.LTE,
                )
            )

        return EvaluationResult(
            evaluation_run_id=evaluation_run_id,
            case_id=case.case_id,
            case_version=case.version,
            evaluator=self.descriptor,
            outcome=EvaluationOutcome.PASSED if passed else EvaluationOutcome.FAILED,
            deterministic_pass=passed,
            score=1.0 if passed else 0.0,
            metrics=tuple(results),
            case_tags=case.tags,
            task_id=observation.task_id,
            run_id=observation.run_id,
            artifact_refs=observation.artifact_refs,
            telemetry_refs=observation.telemetry_refs,
        )


def merge_snapshot_references(
    snapshot: ConfigurationSnapshot,
    references: tuple[VersionReference, ...],
) -> ConfigurationSnapshot:
    """Add runtime-owned identities without discarding richer compatible revisions.

    A caller may already have recorded the same component at the same exact version with
    a more specific commit/revision. That is compatible and is retained. Different
    versions, or two conflicting explicit revisions, are rejected.
    """

    merged = list(snapshot.references)
    positions = {(item.kind, item.ref_id): index for index, item in enumerate(merged)}
    for reference in references:
        identity = (reference.kind, reference.ref_id)
        index = positions.get(identity)
        if index is None:
            positions[identity] = len(merged)
            merged.append(reference)
            continue

        existing = merged[index]
        if existing.version != reference.version:
            raise ValueError(
                "configuration snapshot reference conflicts with runtime-owned version: "
                f"{reference.kind}/{reference.ref_id}"
            )
        if (
            existing.revision is not None
            and reference.revision is not None
            and existing.revision != reference.revision
        ):
            raise ValueError(
                "configuration snapshot reference conflicts with runtime-owned revision: "
                f"{reference.kind}/{reference.ref_id}"
            )
        if existing.revision is None and reference.revision is not None:
            merged[index] = reference
    return replace(snapshot, references=tuple(merged))


def validate_snapshot_reference_kinds(
    snapshot: ConfigurationSnapshot,
    required_kinds: tuple[str, ...],
) -> None:
    """Require deployment/suite-relevant component kinds before execution starts."""

    normalized = tuple(kind.strip() for kind in required_kinds)
    if any(not kind for kind in normalized):
        raise ValueError("required configuration snapshot reference kinds must not be blank")
    if len(normalized) != len(set(normalized)):
        raise ValueError("required configuration snapshot reference kinds must be unique")
    present = {reference.kind for reference in snapshot.references}
    missing = tuple(kind for kind in normalized if kind not in present)
    if missing:
        raise ValueError(
            "configuration snapshot is missing required component reference kinds: "
            + ", ".join(missing)
        )


__all__ = [
    "ResourceLimitEvaluator",
    "merge_snapshot_references",
    "observation_assertion_payload",
    "validate_snapshot_reference_kinds",
]
