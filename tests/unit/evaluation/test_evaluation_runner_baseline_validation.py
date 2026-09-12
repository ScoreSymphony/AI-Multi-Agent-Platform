from __future__ import annotations

import asyncio

import pytest

from ai_multi_agent_platform.evaluation import (
    ConfigurationSnapshot,
    DeterministicAssertionEvaluator,
    EvaluationAttempt,
    EvaluationCase,
    EvaluationExecutionContext,
    EvaluationObservation,
    EvaluationRunner,
    EvaluationSuite,
    InMemoryEvaluationRepository,
    RegressionPolicy,
)


class TrackingExecutor:
    def __init__(self) -> None:
        self.called = False

    async def execute_case(
        self,
        *,
        case: EvaluationCase,
        attempt: EvaluationAttempt,
        execution_context: EvaluationExecutionContext,
    ) -> EvaluationObservation:
        del case, attempt, execution_context
        self.called = True
        return EvaluationObservation()


def test_missing_baseline_is_rejected_before_case_execution() -> None:
    async def scenario() -> None:
        executor = TrackingExecutor()
        runner = EvaluationRunner(
            repository=InMemoryEvaluationRepository(),
            executor=executor,
            evaluators=(DeterministicAssertionEvaluator(),),
        )
        suite = EvaluationSuite(
            suite_id="suite.baseline-validation",
            name="Baseline validation",
            version="1",
            cases=(EvaluationCase(case_id="case.one", name="One", version="1"),),
        )

        with pytest.raises(ValueError, match="baseline run not found"):
            await runner.run_suite(
                suite=suite,
                snapshot=ConfigurationSnapshot(platform_version="0.0.1"),
                baseline_run_id="evaluation_run_missing",
                regression_policy=RegressionPolicy(
                    policy_id="policy.baseline-validation",
                    version="1",
                    rules=(),
                ),
            )

        assert executor.called is False

    asyncio.run(scenario())
