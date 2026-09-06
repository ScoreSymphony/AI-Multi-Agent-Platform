from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ai_multi_agent_platform.coordination import (
    CoordinationPhase,
    InMemoryCoordinatorRepository,
    StepCoordinationRecord,
    StepRetryPolicy,
    StepWait,
    WaitType,
)
from ai_multi_agent_platform.domain import OwnerRef, Plan, Step, StepStatus, new_id
from ai_multi_agent_platform.evaluation import (
    ComparisonOperator,
    CoordinationEvaluationEvidenceProvider,
    DeterministicAssertion,
    DeterministicAssertionEvaluator,
    EvaluationCase,
    EvaluationObservation,
    EvaluationOutcome,
    MetricRule,
    MetricThresholdEvaluator,
)


def test_coordination_state_is_deterministic_evaluation_evidence() -> None:
    owner = OwnerRef(type="user", id="evaluation-user")
    plan = Plan(
        task_id=new_id("task"),
        owner_ref=owner,
        active=True,
        project_id=new_id("project"),
    )
    waiting = Step(
        plan_id=plan.id,
        title="waiting",
        owner_ref=owner,
        project_id=plan.project_id,
        status=StepStatus.WAITING,
    )
    retrying = Step(
        plan_id=plan.id,
        title="retrying",
        owner_ref=owner,
        project_id=plan.project_id,
        status=StepStatus.FAILED,
    )
    barrier = Step(
        plan_id=plan.id,
        title="partial barrier",
        owner_ref=owner,
        project_id=plan.project_id,
        depends_on=(waiting.id, retrying.id),
    )
    now = datetime(2026, 9, 6, 10, 0, tzinfo=UTC)
    wait = StepWait(
        wait_key="evaluation-wait",
        wait_type=WaitType.DEADLINE,
        task_id=plan.task_id,
        plan_id=plan.id,
        step_id=waiting.id,
        owner_ref=owner,
        project_id=plan.project_id,
        deadline_at=now + timedelta(minutes=10),
        created_at=now,
    )
    repository = InMemoryCoordinatorRepository()
    repository.create_plan(
        plan,
        (waiting, retrying, barrier),
        (
            StepCoordinationRecord(
                task_id=plan.task_id,
                plan_id=plan.id,
                plan_revision=plan.revision,
                step_id=waiting.id,
                phase=CoordinationPhase.WAITING,
                wait=wait,
                latest_run_id="run-waiting",
            ),
            StepCoordinationRecord(
                task_id=plan.task_id,
                plan_id=plan.id,
                plan_revision=plan.revision,
                step_id=retrying.id,
                phase=CoordinationPhase.RETRY_SCHEDULED,
                current_attempt=1,
                retry_policy=StepRetryPolicy(
                    max_attempts=2,
                    initial_delay_seconds=30,
                    retryable_categories=("transient",),
                    version=7,
                ),
                retry_due_at=now + timedelta(seconds=30),
            ),
            StepCoordinationRecord(
                task_id=plan.task_id,
                plan_id=plan.id,
                plan_revision=plan.revision,
                step_id=barrier.id,
                phase=CoordinationPhase.BLOCKED,
                dependency_ids=barrier.depends_on,
                satisfied_dependency_ids=(waiting.id,),
            ),
        ),
    )

    evidence = CoordinationEvaluationEvidenceProvider(repository).collect(
        task_id=plan.task_id,
        run_id="evaluation-run-subject",
    )
    observation = EvaluationObservation(
        data=evidence.data,
        metrics=evidence.metrics,
        task_id=plan.task_id,
        run_id="evaluation-run-subject",
    )
    case = EvaluationCase(
        case_id="coordination-runtime-evidence",
        name="Durable coordination evidence remains deterministic",
        version="1.0",
        assertions=(
            DeterministicAssertion(
                assertion_id="plan",
                path="coordination_evidence.plan_id",
                operator=ComparisonOperator.EQ,
                expected=plan.id,
            ),
            DeterministicAssertion(
                assertion_id="wait",
                path="coordination_evidence.steps.0.phase",
                operator=ComparisonOperator.EQ,
                expected=CoordinationPhase.WAITING.value,
            ),
        ),
        metric_rules=(
            MetricRule(
                rule_id="waiting-count",
                metric_name="coordination:waiting_steps",
                operator=ComparisonOperator.EQ,
                threshold=1.0,
            ),
            MetricRule(
                rule_id="retry-count",
                metric_name="coordination:retry_scheduled_steps",
                operator=ComparisonOperator.EQ,
                threshold=1.0,
            ),
            MetricRule(
                rule_id="partial-barrier-count",
                metric_name="coordination:partial_barriers",
                operator=ComparisonOperator.EQ,
                threshold=1.0,
            ),
        ),
        tags=("coordination", "durability", "regression"),
    )

    deterministic = DeterministicAssertionEvaluator().evaluate(
        evaluation_run_id="evaluation-run-384",
        case=case,
        observation=observation,
    )
    metrics = MetricThresholdEvaluator().evaluate(
        evaluation_run_id="evaluation-run-384",
        case=case,
        observation=observation,
    )
    assert deterministic.outcome is EvaluationOutcome.PASSED
    assert metrics.outcome is EvaluationOutcome.PASSED
