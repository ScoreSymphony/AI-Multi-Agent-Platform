"""Issue #502 comparative evaluation matrix without fabricated measurements.

The existing #19 executor records schema/provenance/query metrics. This module defines the broader
representative-workflow evidence required before an enhanced provider can be adopted. Every field is
optional until actually measured; absence remains explicit rather than being converted into zero.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from enum import StrEnum

from ai_multi_agent_platform.contracts.types import JsonValue


class RepositoryIntelligenceMetric(StrEnum):
    TIME_TO_USEFUL_CONTEXT_MS = "time_to_useful_context_ms"
    TASK_SUCCESS = "task_success"
    FIRST_PASS_SUCCESS = "first_pass_success"
    AGENT_TOOL_CALLS = "agent_tool_calls"
    BROAD_FULL_FILE_READS = "broad_full_file_reads"
    BACKTRACKING_STEPS = "backtracking_steps"
    MODEL_CONTEXT_BYTES = "model_context_bytes"
    MODEL_CONTEXT_TOKENS = "model_context_tokens"
    SOURCE_PROVENANCE_ACCURACY = "source_provenance_accuracy"
    SOURCE_SLICE_ACCURACY = "source_slice_accuracy"
    SYMBOL_CORRECTNESS = "symbol_correctness"
    REFERENCE_CORRECTNESS = "reference_correctness"
    DEPENDENCY_CORRECTNESS = "dependency_correctness"
    ARCHITECTURE_USEFULNESS = "architecture_usefulness"
    DOMAIN_USEFULNESS = "domain_usefulness"
    IMPACT_USEFULNESS = "impact_usefulness"
    DIRTY_WORKSPACE_FRESHNESS = "dirty_workspace_freshness"
    INITIAL_INDEX_MS = "initial_index_ms"
    INCREMENTAL_UPDATE_MS = "incremental_update_ms"
    FULL_REBUILD_MS = "full_rebuild_ms"
    QUERY_LATENCY_MS = "query_latency_ms"
    STARTUP_LATENCY_MS = "startup_latency_ms"
    CPU_SECONDS = "cpu_seconds"
    PEAK_RSS_BYTES = "peak_rss_bytes"
    PROVIDER_STATE_BYTES = "provider_state_bytes"
    REPAIR_ACTIONS = "repair_actions"
    REBUILD_ACTIONS = "rebuild_actions"
    FAILURE_RECOVERY_SUCCESS = "failure_recovery_success"
    NETWORK_REQUIRED = "network_required"
    SECRET_REQUIRED = "secret_required"
    EXTERNAL_SERVICE_REQUIRED = "external_service_required"
    PAID_SERVICE_REQUIRED = "paid_service_required"


_RATIO_FIELDS = frozenset(
    {
        "source_provenance_accuracy",
        "source_slice_accuracy",
        "symbol_correctness",
        "reference_correctness",
        "dependency_correctness",
        "architecture_usefulness",
        "domain_usefulness",
        "impact_usefulness",
    }
)
_NON_NEGATIVE_FIELDS = frozenset(
    {
        "time_to_useful_context_ms",
        "agent_tool_calls",
        "broad_full_file_reads",
        "backtracking_steps",
        "model_context_bytes",
        "model_context_tokens",
        "initial_index_ms",
        "incremental_update_ms",
        "full_rebuild_ms",
        "query_latency_ms",
        "startup_latency_ms",
        "cpu_seconds",
        "peak_rss_bytes",
        "provider_state_bytes",
        "repair_actions",
        "rebuild_actions",
    }
)


@dataclass(frozen=True, slots=True)
class RepositoryIntelligenceEvaluationObservation:
    """One provider/workflow observation; ``None`` means genuinely unmeasured."""

    provider_id: str
    fixture_id: str
    source_revision: str
    environment_ref: str
    time_to_useful_context_ms: float | None = None
    task_success: bool | None = None
    first_pass_success: bool | None = None
    agent_tool_calls: int | None = None
    broad_full_file_reads: int | None = None
    backtracking_steps: int | None = None
    model_context_bytes: int | None = None
    model_context_tokens: int | None = None
    source_provenance_accuracy: float | None = None
    source_slice_accuracy: float | None = None
    symbol_correctness: float | None = None
    reference_correctness: float | None = None
    dependency_correctness: float | None = None
    architecture_usefulness: float | None = None
    domain_usefulness: float | None = None
    impact_usefulness: float | None = None
    dirty_workspace_freshness: bool | None = None
    initial_index_ms: float | None = None
    incremental_update_ms: float | None = None
    full_rebuild_ms: float | None = None
    query_latency_ms: float | None = None
    startup_latency_ms: float | None = None
    cpu_seconds: float | None = None
    peak_rss_bytes: int | None = None
    provider_state_bytes: int | None = None
    repair_actions: int | None = None
    rebuild_actions: int | None = None
    failure_recovery_success: bool | None = None
    network_required: bool | None = None
    secret_required: bool | None = None
    external_service_required: bool | None = None
    paid_service_required: bool | None = None
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for value, name in (
            (self.provider_id, "provider_id"),
            (self.fixture_id, "fixture_id"),
            (self.source_revision, "source_revision"),
            (self.environment_ref, "environment_ref"),
        ):
            if not value.strip():
                raise ValueError(f"repository-intelligence evaluation {name} must not be blank")
        for name in _NON_NEGATIVE_FIELDS:
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ValueError(f"repository-intelligence metric {name} must be non-negative")
        for name in _RATIO_FIELDS:
            value = getattr(self, name)
            if value is not None and not 0.0 <= value <= 1.0:
                raise ValueError(f"repository-intelligence metric {name} must be between 0 and 1")
        if any(not note.strip() for note in self.notes):
            raise ValueError("repository-intelligence evaluation notes must not be blank")

    @property
    def measured_metric_names(self) -> tuple[str, ...]:
        return tuple(
            field.name
            for field in fields(self)
            if field.name
            not in {"provider_id", "fixture_id", "source_revision", "environment_ref", "notes"}
            and getattr(self, field.name) is not None
        )

    @property
    def missing_metric_names(self) -> tuple[str, ...]:
        measured = set(self.measured_metric_names)
        return tuple(
            metric.value for metric in RepositoryIntelligenceMetric if metric.value not in measured
        )

    def to_json(self) -> dict[str, JsonValue]:
        metrics: dict[str, JsonValue] = {}
        for metric in RepositoryIntelligenceMetric:
            value = getattr(self, metric.value)
            metrics[metric.value] = value
        return {
            "provider_id": self.provider_id,
            "fixture_id": self.fixture_id,
            "source_revision": self.source_revision,
            "environment_ref": self.environment_ref,
            "metrics": metrics,
            "measured_metrics": list(self.measured_metric_names),
            "missing_metrics": list(self.missing_metric_names),
            "notes": list(self.notes),
        }


@dataclass(frozen=True, slots=True)
class RepositoryIntelligenceMetricDelta:
    metric: RepositoryIntelligenceMetric
    baseline: float | int | bool | None
    candidate: float | int | bool | None
    delta: float | None


@dataclass(frozen=True, slots=True)
class RepositoryIntelligenceComparison:
    baseline_provider_id: str
    candidate_provider_id: str
    fixture_id: str
    comparable: bool
    non_comparability_reasons: tuple[str, ...]
    metrics: tuple[RepositoryIntelligenceMetricDelta, ...]


def compare_repository_intelligence_observations(
    baseline: RepositoryIntelligenceEvaluationObservation,
    candidate: RepositoryIntelligenceEvaluationObservation,
) -> RepositoryIntelligenceComparison:
    reasons: list[str] = []
    if baseline.fixture_id != candidate.fixture_id:
        reasons.append("fixture_id differs")
    if baseline.source_revision != candidate.source_revision:
        reasons.append("source_revision differs")
    if baseline.environment_ref != candidate.environment_ref:
        reasons.append("environment_ref differs")

    deltas: list[RepositoryIntelligenceMetricDelta] = []
    for metric in RepositoryIntelligenceMetric:
        base_value = getattr(baseline, metric.value)
        candidate_value = getattr(candidate, metric.value)
        delta: float | None = None
        if (
            base_value is not None
            and candidate_value is not None
            and not isinstance(base_value, bool)
            and not isinstance(candidate_value, bool)
        ):
            delta = float(candidate_value) - float(base_value)
        deltas.append(
            RepositoryIntelligenceMetricDelta(
                metric=metric,
                baseline=base_value,
                candidate=candidate_value,
                delta=delta,
            )
        )
    return RepositoryIntelligenceComparison(
        baseline_provider_id=baseline.provider_id,
        candidate_provider_id=candidate.provider_id,
        fixture_id=baseline.fixture_id,
        comparable=not reasons,
        non_comparability_reasons=tuple(reasons),
        metrics=tuple(deltas),
    )


__all__ = [
    "RepositoryIntelligenceComparison",
    "RepositoryIntelligenceEvaluationObservation",
    "RepositoryIntelligenceMetric",
    "RepositoryIntelligenceMetricDelta",
    "compare_repository_intelligence_observations",
]
