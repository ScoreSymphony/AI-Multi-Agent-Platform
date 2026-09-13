from __future__ import annotations

import asyncio

from ai_multi_agent_platform.contracts.types import OperationContext, ToolResult
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.evaluation import (
    ComparisonOperator,
    ConfigurationSnapshot,
    DeterministicAssertion,
    DeterministicAssertionEvaluator,
    EvaluationAttempt,
    EvaluationCase,
    EvaluationExecutionContext,
    EvaluationOutcome,
    EvaluationRunner,
    EvaluationSuite,
    InMemoryEvaluationRepository,
)
from ai_multi_agent_platform.evaluation.repository_intelligence import (
    RepositoryIntelligenceEvaluationCaseExecutor,
)
from ai_multi_agent_platform.repositories import RepositoryTree, RepositoryTreeEntry
from ai_multi_agent_platform.repository_intelligence import BaselineRepositoryIntelligenceProvider

_SHA = "a" * 40
_REPOSITORY_ID = new_id("external_resource")


async def _snapshot(
    repository_id: str,
    revision: str,
    context: OperationContext,
) -> RepositoryTree:
    del context
    return RepositoryTree(
        repository_id=repository_id,
        requested_ref=revision,
        resolved_revision=_SHA,
        entries=(
            RepositoryTreeEntry("README.md", b"# Fixture\nneedle here\n"),
            RepositoryTreeEntry("src/demo.py", b"def demo():\n    return 'Needle'\n"),
            RepositoryTreeEntry("assets/blob.bin", b"\xff\x00"),
        ),
    )


def _suite() -> EvaluationSuite:
    common = (
        DeterministicAssertion(
            assertion_id="contract-valid",
            path="contract_valid",
            operator=ComparisonOperator.EQ,
            expected=True,
        ),
        DeterministicAssertion(
            assertion_id="provenance-valid",
            path="source_provenance_valid",
            operator=ComparisonOperator.EQ,
            expected=True,
        ),
        DeterministicAssertion(
            assertion_id="freshness-current",
            path="freshness_current",
            operator=ComparisonOperator.EQ,
            expected=True,
        ),
    )
    return EvaluationSuite(
        suite_id="suite.repository-intelligence-contract",
        name="Repository intelligence provider contract",
        version="1",
        tags=("repository-intelligence", "issue-502", "no-paid-service"),
        cases=(
            EvaluationCase(
                case_id="case.repository-map",
                name="Bounded repository map",
                version="1",
                input_template={
                    "operation": "repository.map",
                    "arguments": {
                        "repository_id": _REPOSITORY_ID,
                        "revision": "fixture",
                        "max_entries": 10,
                    },
                },
                assertions=common
                + (
                    DeterministicAssertion(
                        assertion_id="map-count",
                        path="output.returned_entries",
                        operator=ComparisonOperator.EQ,
                        expected=3,
                    ),
                ),
            ),
            EvaluationCase(
                case_id="case.repository-search",
                name="Deterministic repository text search",
                version="1",
                input_template={
                    "operation": "repository.text_search",
                    "arguments": {
                        "repository_id": _REPOSITORY_ID,
                        "revision": "fixture",
                        "query": "needle",
                        "max_results": 10,
                    },
                },
                assertions=common
                + (
                    DeterministicAssertion(
                        assertion_id="search-first-path",
                        path="output.hits.0.path",
                        operator=ComparisonOperator.EQ,
                        expected="README.md",
                    ),
                    DeterministicAssertion(
                        assertion_id="search-second-path",
                        path="output.hits.1.path",
                        operator=ComparisonOperator.EQ,
                        expected="src/demo.py",
                    ),
                ),
            ),
            EvaluationCase(
                case_id="case.repository-source-slice",
                name="Exact repository source slice",
                version="1",
                input_template={
                    "operation": "repository.source_slice",
                    "arguments": {
                        "repository_id": _REPOSITORY_ID,
                        "revision": "fixture",
                        "path": "src/demo.py",
                        "start_line": 2,
                        "end_line": 2,
                    },
                },
                assertions=common
                + (
                    DeterministicAssertion(
                        assertion_id="slice-text",
                        path="output.lines.0.text",
                        operator=ComparisonOperator.EQ,
                        expected="    return 'Needle'",
                    ),
                ),
            ),
        ),
    )


def _run(provider_id: str) -> tuple[EvaluationOutcome, ...]:
    async def scenario() -> tuple[EvaluationOutcome, ...]:
        provider = BaselineRepositoryIntelligenceProvider(_snapshot, provider_id=provider_id)
        repository = InMemoryEvaluationRepository()
        runner = EvaluationRunner(
            repository=repository,
            executor=RepositoryIntelligenceEvaluationCaseExecutor(provider),
            evaluators=(DeterministicAssertionEvaluator(),),
        )
        summary = await runner.run_suite(
            suite=_suite(),
            snapshot=ConfigurationSnapshot(platform_version="0.0.1"),
        )
        assert summary.run.status.value == "completed"
        assert len(summary.results) == 3
        for result in summary.results:
            metric_names = {metric.metric_name for metric in result.metrics}
            # The deterministic evaluator does not copy observation metrics. They remain available
            # to MetricThresholdEvaluator in the same canonical EvaluationRunner path.
            assert metric_names == set()
        return tuple(result.outcome for result in summary.results)

    return asyncio.run(scenario())


def test_same_versioned_suite_accepts_baseline_and_replaceable_provider_identity() -> None:
    baseline = _run("platform.repository-intelligence.baseline")
    candidate = _run("pilot.repository-intelligence.projectatlas")

    assert baseline == (EvaluationOutcome.PASSED,) * 3
    assert candidate == baseline


def test_executor_records_schema_provenance_and_resource_metrics() -> None:
    async def scenario() -> None:
        provider = BaselineRepositoryIntelligenceProvider(_snapshot)
        executor = RepositoryIntelligenceEvaluationCaseExecutor(provider)
        case = _suite().cases[1]
        attempt = EvaluationAttempt(
            evaluation_run_id="evaluation_run_issue502",
            case_id=case.case_id,
            case_version=case.version,
            repetition_index=0,
        )
        observation = await executor.execute_case(
            case=case,
            attempt=attempt,
            execution_context=EvaluationExecutionContext(attempt_id=attempt.attempt_id),
        )

        assert observation.data["contract_valid"] is True
        assert observation.data["source_provenance_valid"] is True
        assert observation.data["freshness_current"] is True
        assert observation.data["resolved_revision"] == _SHA
        assert observation.metrics["query_latency_ms"] >= 0
        assert observation.metrics["result_bytes"] > 0
        assert observation.metrics["schema_error_count"] == 0
        assert observation.capability_refs == ("repository.text_search",)

    asyncio.run(scenario())


def test_executor_marks_malformed_source_output_as_noncompliant() -> None:
    class MalformedProvider(BaselineRepositoryIntelligenceProvider):
        async def invoke(self, invocation):  # type: ignore[no-untyped-def]
            return ToolResult(invocation_id=invocation.invocation_id, output={"entries": []})

    async def scenario() -> None:
        executor = RepositoryIntelligenceEvaluationCaseExecutor(MalformedProvider(_snapshot))
        case = _suite().cases[0]
        attempt = EvaluationAttempt(
            evaluation_run_id="evaluation_run_issue502_malformed",
            case_id=case.case_id,
            case_version=case.version,
            repetition_index=0,
        )
        observation = await executor.execute_case(
            case=case,
            attempt=attempt,
            execution_context=EvaluationExecutionContext(attempt_id=attempt.attempt_id),
        )

        assert observation.data["contract_valid"] is False
        assert observation.data["source_provenance_valid"] is False
        assert observation.data["freshness_current"] is False
        assert observation.metrics["schema_error_count"] > 0

    asyncio.run(scenario())
