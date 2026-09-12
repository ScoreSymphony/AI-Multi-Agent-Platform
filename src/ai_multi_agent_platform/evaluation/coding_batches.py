"""Deterministic #19 Evaluation adapter for parallel coding batches (#872)."""

from __future__ import annotations

from ai_multi_agent_platform.coding_batches import (
    CodingBatchCoordinator,
    IntegrationState,
    RepairAttemptState,
    WorkstreamState,
)
from ai_multi_agent_platform.contracts.types import JsonValue

from .context import EvaluationExecutionContext
from .models import (
    ComparisonOperator,
    DeterministicAssertion,
    EvaluationAttempt,
    EvaluationCase,
    EvaluationObservation,
    EvaluationSuite,
    MetricRule,
)

CODING_BATCH_QUALITY_SUITE_ID = "suite.parallel-coding-batch-quality"
CODING_BATCH_QUALITY_SUITE_VERSION = "1"


class CodingBatchEvaluationCaseExecutor:
    """Project canonical #872 state into the existing deterministic #19 framework.

    The executor is read-only. It does not schedule Steps, dispatch Agents, mutate Workspaces,
    perform Git operations, create Verification evidence or authorize integration. Those facts
    remain owned by their canonical subsystems and are only measured here.
    """

    def __init__(self, coordinator: CodingBatchCoordinator) -> None:
        self._coordinator = coordinator

    async def execute_case(
        self,
        *,
        case: EvaluationCase,
        attempt: EvaluationAttempt,
        execution_context: EvaluationExecutionContext,
    ) -> EvaluationObservation:
        if execution_context.attempt_id != attempt.attempt_id:
            raise ValueError("evaluation execution context belongs to another attempt")

        batch_id = _required_string(case, "batch_id")
        integration_id = _required_string(case, "integration_id")
        expect_repair = _required_bool(case, "expect_repair")
        batch = self._coordinator.get(batch_id)
        candidate = batch.integration_candidate(integration_id)
        workstreams = batch.workstreams

        accepted_states = {WorkstreamState.ACCEPTED, WorkstreamState.INTEGRATED}
        accepted_count = sum(item.state in accepted_states for item in workstreams)
        verified_count = sum(
            item.verification is not None
            and item.verification.passed
            and item.result is not None
            and item.verification.subject_revision == item.result.output_revision
            for item in workstreams
        )
        workspace_ids = tuple(
            item.provenance.workspace_id
            for item in workstreams
            if item.provenance.workspace_id is not None
        )
        agent_run_ids = tuple(
            item.provenance.agent_run_id
            for item in workstreams
            if item.provenance.agent_run_id is not None
        )

        validation = candidate.validation
        combined_validation_exact = (
            validation is not None
            and candidate.integrated_revision is not None
            and validation.subject_revision == candidate.integrated_revision
            and validation.passes
            and all(
                check.revision == candidate.integrated_revision
                for check in validation.required_checks
            )
        )
        repair_attempts = candidate.repair_attempts
        verified_repairs = sum(
            item.state is RepairAttemptState.VERIFIED
            and item.verification is not None
            and item.verification.passed
            and item.output_revision is not None
            and item.verification.subject_revision == item.output_revision
            for item in repair_attempts
        )
        repair_expectation_satisfied = bool(repair_attempts) is expect_repair

        data: dict[str, JsonValue] = {
            "batch_id": batch.batch_id,
            "integration_id": candidate.integration_id,
            "workstream_count": len(workstreams),
            "accepted_workstream_count": accepted_count,
            "verified_workstream_count": verified_count,
            "workspace_count": len(workspace_ids),
            "unique_workspace_count": len(set(workspace_ids)),
            "agent_run_count": len(agent_run_ids),
            "unique_agent_run_count": len(set(agent_run_ids)),
            "integration_state": candidate.state.value,
            "combined_validation_exact": combined_validation_exact,
            "stale_base": candidate.stale_base,
            "unresolved_conflict_count": len(candidate.conflicts),
            "repair_attempt_count": len(repair_attempts),
            "verified_repair_count": verified_repairs,
            "expect_repair": expect_repair,
            "repair_expectation_satisfied": repair_expectation_satisfied,
        }
        metrics = {
            "workstream_acceptance_rate": _ratio(accepted_count, len(workstreams)),
            "workstream_verification_rate": _ratio(verified_count, len(workstreams)),
            "workspace_isolation_rate": _isolation_rate(workspace_ids, len(workstreams)),
            "agent_run_provenance_rate": _isolation_rate(agent_run_ids, len(workstreams)),
            "combined_validation_rate": 1.0 if combined_validation_exact else 0.0,
            "stale_base_rate": 1.0 if candidate.stale_base else 0.0,
            "unresolved_conflict_rate": 1.0 if candidate.conflicts else 0.0,
            "repair_verification_rate": (
                1.0 if not repair_attempts else _ratio(verified_repairs, len(repair_attempts))
            ),
            "repair_expectation_rate": 1.0 if repair_expectation_satisfied else 0.0,
        }
        return EvaluationObservation(data=data, metrics=metrics)


def canonical_coding_batch_quality_suite(
    batch_id: str,
    integration_id: str,
    *,
    expect_repair: bool = False,
) -> EvaluationSuite:
    """Return the strict local/no-paid-service #872 quality suite for one integration."""

    if not batch_id.strip():
        raise ValueError("batch_id must not be blank")
    if not integration_id.strip():
        raise ValueError("integration_id must not be blank")
    return EvaluationSuite(
        suite_id=CODING_BATCH_QUALITY_SUITE_ID,
        name="Parallel coding batch integration quality",
        version=CODING_BATCH_QUALITY_SUITE_VERSION,
        description=(
            "Deterministic #19 regression evidence for isolated coding workstreams, exact "
            "Verification provenance, conflict/repair closure and fresh combined validation."
        ),
        tags=("coding-batch", "issue-872", "deterministic", "no-paid-service"),
        cases=(
            EvaluationCase(
                case_id="case.parallel-coding-batch-integration",
                name="Parallel coding batch integration is decision-ready",
                version="1",
                input_template={
                    "batch_id": batch_id,
                    "integration_id": integration_id,
                    "expect_repair": expect_repair,
                },
                tags=("coding-batch", "integration", "provenance"),
                category="coding-batch-quality",
                assertions=(
                    DeterministicAssertion(
                        assertion_id="batch-id",
                        path="batch_id",
                        operator=ComparisonOperator.EQ,
                        expected=batch_id,
                    ),
                    DeterministicAssertion(
                        assertion_id="integration-id",
                        path="integration_id",
                        operator=ComparisonOperator.EQ,
                        expected=integration_id,
                    ),
                    DeterministicAssertion(
                        assertion_id="combined-validation-exact",
                        path="combined_validation_exact",
                        operator=ComparisonOperator.EQ,
                        expected=True,
                    ),
                    DeterministicAssertion(
                        assertion_id="not-stale",
                        path="stale_base",
                        operator=ComparisonOperator.EQ,
                        expected=False,
                    ),
                    DeterministicAssertion(
                        assertion_id="no-unresolved-conflicts",
                        path="unresolved_conflict_count",
                        operator=ComparisonOperator.EQ,
                        expected=0,
                    ),
                    DeterministicAssertion(
                        assertion_id="repair-expectation",
                        path="repair_expectation_satisfied",
                        operator=ComparisonOperator.EQ,
                        expected=True,
                    ),
                ),
                metric_rules=(
                    MetricRule(
                        rule_id="workstream-acceptance",
                        metric_name="workstream_acceptance_rate",
                        operator=ComparisonOperator.GTE,
                        threshold=1.0,
                    ),
                    MetricRule(
                        rule_id="workstream-verification",
                        metric_name="workstream_verification_rate",
                        operator=ComparisonOperator.GTE,
                        threshold=1.0,
                    ),
                    MetricRule(
                        rule_id="workspace-isolation",
                        metric_name="workspace_isolation_rate",
                        operator=ComparisonOperator.GTE,
                        threshold=1.0,
                    ),
                    MetricRule(
                        rule_id="agent-run-provenance",
                        metric_name="agent_run_provenance_rate",
                        operator=ComparisonOperator.GTE,
                        threshold=1.0,
                    ),
                    MetricRule(
                        rule_id="combined-validation",
                        metric_name="combined_validation_rate",
                        operator=ComparisonOperator.GTE,
                        threshold=1.0,
                    ),
                    MetricRule(
                        rule_id="stale-base",
                        metric_name="stale_base_rate",
                        operator=ComparisonOperator.LTE,
                        threshold=0.0,
                    ),
                    MetricRule(
                        rule_id="unresolved-conflicts",
                        metric_name="unresolved_conflict_rate",
                        operator=ComparisonOperator.LTE,
                        threshold=0.0,
                    ),
                    MetricRule(
                        rule_id="repair-verification",
                        metric_name="repair_verification_rate",
                        operator=ComparisonOperator.GTE,
                        threshold=1.0,
                    ),
                    MetricRule(
                        rule_id="repair-expectation",
                        metric_name="repair_expectation_rate",
                        operator=ComparisonOperator.GTE,
                        threshold=1.0,
                    ),
                ),
            ),
        ),
    )


def _required_string(case: EvaluationCase, key: str) -> str:
    value = case.input_template.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"coding batch evaluation case requires input_template.{key}")
    return value


def _required_bool(case: EvaluationCase, key: str) -> bool:
    value = case.input_template.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"coding batch evaluation case requires boolean input_template.{key}")
    return value


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return numerator / denominator


def _isolation_rate(values: tuple[str, ...], expected_count: int) -> float:
    if expected_count <= 0:
        return 0.0
    if len(values) != expected_count:
        return len(values) / expected_count
    return len(set(values)) / expected_count


__all__ = [
    "CODING_BATCH_QUALITY_SUITE_ID",
    "CODING_BATCH_QUALITY_SUITE_VERSION",
    "CodingBatchEvaluationCaseExecutor",
    "canonical_coding_batch_quality_suite",
]
