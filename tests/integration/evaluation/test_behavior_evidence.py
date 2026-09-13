from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from ai_multi_agent_platform.contracts import ExecutionRequest, OperationContext
from ai_multi_agent_platform.distributed import (
    DispatchRecord,
    DispatchState,
    DistributedRegistry,
    DistributedRuntime,
    JobRequirements,
    NodeRecord,
    RegistrationRequest,
    ResourceSnapshot,
    WorkerJobRequest,
    WorkerRecord,
)
from ai_multi_agent_platform.domain import Approval, ApprovalStatus, OwnerRef, new_id
from ai_multi_agent_platform.evaluation import (
    ApprovalEvidenceCaseExecutor,
    ComparisonOperator,
    DeterministicAssertion,
    DeterministicAssertionEvaluator,
    DistributedRuntimeEvidenceCaseExecutor,
    EvaluationAttempt,
    EvaluationCase,
    EvaluationExecutionContext,
    EvaluationObservation,
    EvaluationOutcome,
)
from ai_multi_agent_platform.security import ApprovalRecord, RiskClassification

NOW = datetime(2026, 9, 5, 16, 0, tzinfo=UTC)


class StaticExecutor:
    def __init__(self, observation: EvaluationObservation) -> None:
        self.observation = observation

    async def execute_case(
        self,
        *,
        case: EvaluationCase,
        attempt: EvaluationAttempt,
        execution_context: EvaluationExecutionContext,
    ) -> EvaluationObservation:
        del case
        assert execution_context.attempt_id == attempt.attempt_id
        return self.observation


class ApprovalRecords:
    def __init__(self, records: tuple[ApprovalRecord, ...]) -> None:
        self.records = records

    def all(self) -> tuple[ApprovalRecord, ...]:
        return self.records


def _attempt(case: EvaluationCase) -> EvaluationAttempt:
    return EvaluationAttempt(
        evaluation_run_id="evaluation_run_behavior_sources",
        case_id=case.case_id,
        case_version=case.version,
        repetition_index=0,
    )


def test_approval_evidence_is_assertable_from_canonical_records() -> None:
    async def scenario() -> None:
        task_id = new_id("task")
        run_id = new_id("run")
        approval = Approval(
            id=new_id("approval"),
            subject_type="run",
            subject_id=run_id,
            owner_ref=OwnerRef(type="user", id="owner"),
            status=ApprovalStatus.APPROVED,
            reason="governed action",
            decision_by=OwnerRef(type="user", id="reviewer"),
            created_at=NOW,
            updated_at=NOW,
        )
        record = ApprovalRecord(
            approval=approval,
            requester_ref="user:owner",
            action="tool.invoke",
            resource_type="capability",
            resource_id="capability.shell",
            requested_action_digest="digest-1",
            risk=RiskClassification.ELEVATED,
            policy_id="policy.approval",
            expires_at=NOW + timedelta(minutes=15),
            task_id=task_id,
            run_id=run_id,
            capability_ref="capability.shell",
            decision_at=NOW,
            decision_comment="approved for evaluation",
        )
        case = EvaluationCase(
            case_id="case.approval",
            name="approval evidence",
            version="1",
            assertions=(
                DeterministicAssertion(
                    "approval-status",
                    "approval_behavior.records.0.status",
                    ComparisonOperator.EQ,
                    expected="approved",
                ),
                DeterministicAssertion(
                    "approval-capability",
                    "approval_behavior.records.0.capability_ref",
                    ComparisonOperator.EQ,
                    expected="capability.shell",
                ),
            ),
        )
        attempt = _attempt(case)
        executor = ApprovalEvidenceCaseExecutor(
            StaticExecutor(EvaluationObservation(task_id=task_id, run_id=run_id)),
            ApprovalRecords((record,)),
        )
        observation = await executor.execute_case(
            case=case,
            attempt=attempt,
            execution_context=EvaluationExecutionContext(attempt_id=attempt.attempt_id),
        )
        result = DeterministicAssertionEvaluator().evaluate(
            evaluation_run_id=attempt.evaluation_run_id,
            case=case,
            observation=observation,
        )

        assert result.outcome is EvaluationOutcome.PASSED
        assert all(assertion.passed for assertion in result.assertions)

    asyncio.run(scenario())


def test_distributed_evidence_projects_selected_node_and_worker() -> None:
    async def scenario() -> None:
        task_id = new_id("task")
        run_id = new_id("run")
        node = NodeRecord(
            node_id=new_id("node"),
            display_name="evaluation-node",
            resources=ResourceSnapshot(
                cpu_cores_total=8.0,
                cpu_cores_available=8.0,
                ram_total_bytes=16_000,
                ram_available_bytes=16_000,
                storage_total_bytes=100_000,
                storage_available_bytes=100_000,
            ),
            supported_runtimes=("python",),
        )
        worker = WorkerRecord(
            worker_id=new_id("worker"),
            node_id=node.node_id,
            supported_executors=("reference",),
            supported_runtimes=("python",),
            capability_refs=("capability.shell",),
            concurrency_limit=1,
        )
        runtime = DistributedRuntime(DistributedRegistry())
        runtime.register(RegistrationRequest(node=node, workers=(worker,)), now=NOW)
        job = WorkerJobRequest(
            execution=ExecutionRequest(
                run_id=run_id,
                subject_type="task",
                subject_id=task_id,
                context=OperationContext(correlation_id="evaluation-distributed"),
                input={"payload": "test"},
            ),
            requirements=JobRequirements(
                executor_type="reference",
                capability_refs=("capability.shell",),
                runtime="python",
                model_ref="model.local.qwen",
            ),
            dispatch_attempt=2,
        )
        runtime.restore_records(
            (
                DispatchRecord(
                    job=job,
                    worker_id=worker.worker_id,
                    reservation_id=new_id("reservation"),
                    state=DispatchState.RUNNING,
                ),
            )
        )
        case = EvaluationCase(
            case_id="case.distributed",
            name="distributed evidence",
            version="1",
            assertions=(
                DeterministicAssertion(
                    "node",
                    "distributed_behavior.jobs.0.node_id",
                    ComparisonOperator.EQ,
                    expected=node.node_id,
                ),
                DeterministicAssertion(
                    "worker",
                    "distributed_behavior.jobs.0.worker_id",
                    ComparisonOperator.EQ,
                    expected=worker.worker_id,
                ),
                DeterministicAssertion(
                    "dispatch-attempt",
                    "distributed_behavior.jobs.0.dispatch_attempt",
                    ComparisonOperator.EQ,
                    expected=2,
                ),
                DeterministicAssertion(
                    "capability",
                    "distributed_behavior.jobs.0.capability_refs",
                    ComparisonOperator.CONTAINS,
                    expected="capability.shell",
                ),
            ),
        )
        attempt = _attempt(case)
        executor = DistributedRuntimeEvidenceCaseExecutor(
            StaticExecutor(EvaluationObservation(task_id=task_id, run_id=run_id)),
            runtime,
        )
        observation = await executor.execute_case(
            case=case,
            attempt=attempt,
            execution_context=EvaluationExecutionContext(attempt_id=attempt.attempt_id),
        )
        result = DeterministicAssertionEvaluator().evaluate(
            evaluation_run_id=attempt.evaluation_run_id,
            case=case,
            observation=observation,
        )

        assert result.outcome is EvaluationOutcome.PASSED
        assert all(assertion.passed for assertion in result.assertions)

    asyncio.run(scenario())
