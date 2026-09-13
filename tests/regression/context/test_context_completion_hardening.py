from __future__ import annotations

import asyncio
import json
from types import MappingProxyType
from typing import cast

import pytest

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.evaluation import (
    ComparisonOperator,
    ConfigurationSnapshot,
    DeterministicAssertion,
    DeterministicAssertionEvaluator,
    EvaluationAttempt,
    EvaluationCase,
    EvaluationExecutionContext,
    EvaluationObservation,
    EvaluationOutcome,
    EvaluationRunner,
    EvaluationSuite,
    InMemoryEvaluationRepository,
    ResourceLimitEvaluator,
    SnapshotValue,
    VersionReference,
)


class StaticExecutor:
    def __init__(self, observation: EvaluationObservation) -> None:
        self.observation = observation
        self.called = False

    async def execute_case(
        self,
        *,
        case: EvaluationCase,
        attempt: EvaluationAttempt,
        execution_context: EvaluationExecutionContext,
    ) -> EvaluationObservation:
        del case
        assert execution_context.attempt_id == attempt.attempt_id
        self.called = True
        return self.observation


def test_deterministic_assertions_can_address_canonical_non_output_behavior() -> None:
    case = EvaluationCase(
        case_id="case.behavior",
        name="behavior contract",
        version="1",
        assertions=(
            DeterministicAssertion(
                "model",
                "behavior.selected_model_config_id",
                ComparisonOperator.EQ,
                expected="model.local.qwen",
            ),
            DeterministicAssertion(
                "provider",
                "behavior.selected_provider_id",
                ComparisonOperator.EQ,
                expected="provider.local",
            ),
            DeterministicAssertion(
                "capability",
                "behavior.capability_refs",
                ComparisonOperator.CONTAINS,
                expected="capability.shell",
            ),
            DeterministicAssertion(
                "event",
                "behavior.event_types",
                ComparisonOperator.CONTAINS,
                expected="run.succeeded",
            ),
        ),
    )
    result = DeterministicAssertionEvaluator().evaluate(
        evaluation_run_id="evaluation_run_behavior",
        case=case,
        observation=EvaluationObservation(
            task_id="task_1",
            run_id="run_1",
            selected_model_config_id="model.local.qwen",
            selected_provider_id="provider.local",
            capability_refs=("capability.shell",),
            event_types=("run.started", "run.succeeded"),
        ),
    )

    assert result.outcome is EvaluationOutcome.PASSED
    assert all(assertion.passed for assertion in result.assertions)


def test_deterministic_assertion_results_thaw_nested_immutable_domain_evidence() -> None:
    immutable_output = cast(
        JsonValue,
        MappingProxyType(
            {
                "nested": (
                    MappingProxyType({"status": "ok"}),
                    MappingProxyType({"status": "complete"}),
                )
            }
        ),
    )
    case = EvaluationCase(
        case_id="case.strict-json",
        name="strict JSON evidence",
        version="1",
        assertions=(
            DeterministicAssertion(
                "run-output",
                "run.output",
                ComparisonOperator.EXISTS,
            ),
        ),
    )

    result = DeterministicAssertionEvaluator().evaluate(
        evaluation_run_id="evaluation_run_strict_json",
        case=case,
        observation=EvaluationObservation(data={"run": {"output": immutable_output}}),
    )

    actual = result.assertions[0].actual
    assert actual == {
        "nested": [
            {"status": "ok"},
            {"status": "complete"},
        ]
    }
    assert json.loads(json.dumps(actual, allow_nan=False)) == actual


def test_resource_limits_are_deterministic_maximum_metric_checks() -> None:
    case = EvaluationCase(
        case_id="case.resources",
        name="resource limits",
        version="1",
        resource_limits=(
            SnapshotValue("latency_ms", "500"),
            SnapshotValue("dispatch_attempts", "1"),
        ),
    )
    evaluator = ResourceLimitEvaluator()

    passing = evaluator.evaluate(
        evaluation_run_id="evaluation_run_pass",
        case=case,
        observation=EvaluationObservation(metrics={"latency_ms": 250.0, "dispatch_attempts": 1.0}),
    )
    failing = evaluator.evaluate(
        evaluation_run_id="evaluation_run_fail",
        case=case,
        observation=EvaluationObservation(metrics={"latency_ms": 750.0, "dispatch_attempts": 1.0}),
    )
    missing = evaluator.evaluate(
        evaluation_run_id="evaluation_run_missing",
        case=case,
        observation=EvaluationObservation(metrics={"latency_ms": 250.0}),
    )

    assert passing.outcome is EvaluationOutcome.PASSED
    assert failing.outcome is EvaluationOutcome.FAILED
    assert (
        next(metric for metric in failing.metrics if metric.metric_name == "latency_ms").passed
        is False
    )
    assert missing.outcome is EvaluationOutcome.FAILED
    assert {metric.metric_name for metric in missing.metrics} == {"latency_ms"}


def test_runner_persists_resource_limit_result_and_runtime_owned_snapshot_refs() -> None:
    async def scenario() -> None:
        repository = InMemoryEvaluationRepository()
        executor = StaticExecutor(
            EvaluationObservation(
                data={"status": "ok"},
                metrics={"dispatch_attempts": 1.0},
            )
        )
        runner = EvaluationRunner(
            repository=repository,
            executor=executor,
            evaluators=(DeterministicAssertionEvaluator(),),
            configuration_references=(
                VersionReference("orchestrator", "reference", "1"),
                VersionReference("executor", "reference", "1"),
            ),
            required_snapshot_kinds=(
                "orchestrator",
                "executor",
                "evaluation_suite",
                "evaluator",
            ),
        )
        suite = EvaluationSuite(
            suite_id="suite.hardened",
            name="hardened",
            version="1",
            cases=(
                EvaluationCase(
                    case_id="case.hardened",
                    name="hardened case",
                    version="1",
                    assertions=(
                        DeterministicAssertion(
                            "status",
                            "status",
                            ComparisonOperator.EQ,
                            expected="ok",
                        ),
                    ),
                    resource_limits=(SnapshotValue("dispatch_attempts", "1"),),
                ),
            ),
        )

        summary = await runner.run_suite(
            suite=suite,
            snapshot=ConfigurationSnapshot(platform_version="test"),
        )

        assert executor.called is True
        assert {result.evaluator.evaluator_id for result in summary.results} == {
            "reference.deterministic",
            "reference.resource-limit",
        }
        assert all(result.outcome is EvaluationOutcome.PASSED for result in summary.results)
        identities = {
            (reference.kind, reference.ref_id, reference.version)
            for reference in summary.run.snapshot.references
        }
        assert ("evaluation_suite", "suite.hardened", "1") in identities
        assert ("evaluator", "reference.deterministic", "1.0") in identities
        assert ("evaluator", "reference.resource-limit", "1.0") in identities
        assert ("orchestrator", "reference", "1") in identities
        assert ("executor", "reference", "1") in identities

    asyncio.run(scenario())


def test_runner_rejects_missing_required_snapshot_component_before_execution() -> None:
    async def scenario() -> None:
        repository = InMemoryEvaluationRepository()
        executor = StaticExecutor(EvaluationObservation(data={"status": "ok"}))
        runner = EvaluationRunner(
            repository=repository,
            executor=executor,
            evaluators=(DeterministicAssertionEvaluator(),),
            required_snapshot_kinds=("model",),
        )
        suite = EvaluationSuite(
            suite_id="suite.model-required",
            name="model required",
            version="1",
            cases=(EvaluationCase("case.model", "model case", "1"),),
        )

        with pytest.raises(ValueError, match="missing required component reference kinds: model"):
            await runner.run_suite(
                suite=suite,
                snapshot=ConfigurationSnapshot(platform_version="test"),
            )
        assert executor.called is False
        assert repository.list_runs(limit=None) == ()

    asyncio.run(scenario())
