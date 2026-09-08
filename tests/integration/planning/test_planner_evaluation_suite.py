from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

from ai_multi_agent_platform.evaluation import (
    ComparisonOperator,
    ConfigurationSnapshot,
    DeterministicAssertion,
    DeterministicAssertionEvaluator,
    EvaluationOutcome,
    EvaluationRunner,
    InMemoryEvaluationRepository,
    load_evaluation_suite,
)
from ai_multi_agent_platform.evaluation.planning import ReferencePlanningEvaluationCaseExecutor

_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
_SUITE = _REPOSITORY_ROOT / "config" / "evaluation-suite.planning-deterministic.json"


def test_checked_in_planning_suite_runs_through_issue_19_evaluation_runtime() -> None:
    async def scenario() -> None:
        suite = load_evaluation_suite(_SUITE)
        assert suite.suite_id == "suite.planning-deterministic"
        assert suite.version == "1"
        assert len(suite.cases) == 6
        assert {case.input_template["scenario"] for case in suite.cases} == {
            "dependency_parallelism",
            "unavailable_requirement",
            "provider_replacement",
            "duplicate_evidence",
            "bounded_replanning",
            "no_privilege_escalation",
        }

        repository = InMemoryEvaluationRepository()
        runner = EvaluationRunner(
            repository=repository,
            executor=ReferencePlanningEvaluationCaseExecutor(),
            evaluators=(DeterministicAssertionEvaluator(),),
        )
        summary = await runner.run_suite(
            suite=suite,
            snapshot=ConfigurationSnapshot(
                platform_version="0.0.test",
                platform_commit="planner-evaluation-test",
            ),
            seed=439,
        )

        assert len(summary.results) == len(suite.cases)
        assert all(result.outcome is EvaluationOutcome.PASSED for result in summary.results)
        assert all(result.deterministic_pass is True for result in summary.results)
        assert all(result.task_id is not None for result in summary.results)
        assert all(result.run_id is None for result in summary.results)
        assert {result.case_id for result in summary.results} == {
            case.case_id for case in suite.cases
        }
        references = {
            (reference.kind, reference.ref_id) for reference in summary.run.snapshot.references
        }
        assert ("evaluation_suite", suite.suite_id) in references
        assert ("evaluator", "reference.deterministic") in references

    asyncio.run(scenario())


def test_planning_suite_failure_is_reported_as_evaluation_result_not_hidden_by_executor() -> None:
    async def scenario() -> None:
        suite = load_evaluation_suite(_SUITE)
        first = suite.cases[0]
        deliberately_wrong = replace(
            first,
            assertions=(
                *first.assertions,
                DeterministicAssertion(
                    assertion_id="deliberate-regression-sentinel",
                    path="peak_parallelism",
                    operator=ComparisonOperator.EQ,
                    expected=99,
                ),
            ),
        )
        mutated = replace(suite, cases=(deliberately_wrong,))
        runner = EvaluationRunner(
            repository=InMemoryEvaluationRepository(),
            executor=ReferencePlanningEvaluationCaseExecutor(),
            evaluators=(DeterministicAssertionEvaluator(),),
        )
        summary = await runner.run_suite(
            suite=mutated,
            snapshot=ConfigurationSnapshot(platform_version="0.0.test"),
            seed=439,
        )

        assert len(summary.results) == 1
        result = summary.results[0]
        assert result.outcome is EvaluationOutcome.FAILED
        assert result.deterministic_pass is False
        sentinel = next(
            assertion
            for assertion in result.assertions
            if assertion.assertion_id == "deliberate-regression-sentinel"
        )
        assert sentinel.passed is False
        assert sentinel.actual == 2

    asyncio.run(scenario())
