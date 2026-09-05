from __future__ import annotations

import asyncio

from ai_multi_agent_platform.control_plane import ControlPlane, ControlPlaneHTTP, HTTPRequest
from ai_multi_agent_platform.control_plane.evaluation_contract import (
    EVALUATION_RUN_COLLECTION,
    EVALUATION_SUITE_COLLECTION,
    evaluation_command_handlers,
    evaluation_resource_services,
)
from ai_multi_agent_platform.evaluation import (
    AggregationMethod,
    AggregationPolicy,
    ComparisonOperator,
    ConfigurationSnapshot,
    DeterministicAssertion,
    DeterministicAssertionEvaluator,
    EvaluationAttempt,
    EvaluationCase,
    EvaluationExecutionContext,
    EvaluationObservation,
    EvaluationRun,
    EvaluationRunner,
    EvaluationSuite,
    InMemoryEvaluationRepository,
    RegressionPolicy,
    RegressionRule,
    RegressionRuleKind,
)
from ai_multi_agent_platform.evaluation.service import (
    EvaluationService,
    aggregation_policy_ref,
    evaluation_suite_ref,
    regression_policy_ref,
)
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator


class MutableExecutor:
    def __init__(self, status: str = "ok") -> None:
        self.status = status

    async def execute_case(
        self,
        *,
        case: EvaluationCase,
        attempt: EvaluationAttempt,
        execution_context: EvaluationExecutionContext,
    ) -> EvaluationObservation:
        del case, attempt, execution_context
        return EvaluationObservation(data={"status": self.status})


def _evaluation_stack() -> tuple[
    EvaluationService,
    MutableExecutor,
    InMemoryEvaluationRepository,
]:
    repository = InMemoryEvaluationRepository()
    executor = MutableExecutor()
    runner = EvaluationRunner(
        repository=repository,
        executor=executor,
        evaluators=(DeterministicAssertionEvaluator(),),
    )
    suite = EvaluationSuite(
        suite_id="reference.lifecycle",
        name="Reference lifecycle",
        version="1.0",
        cases=(
            EvaluationCase(
                case_id="status-check",
                name="Status check",
                version="1.0",
                assertions=(
                    DeterministicAssertion(
                        assertion_id="status-ok",
                        path="status",
                        operator=ComparisonOperator.EQ,
                        expected="ok",
                    ),
                ),
                tags=("critical",),
            ),
        ),
    )
    policy = RegressionPolicy(
        policy_id="reference.pr",
        version="1.0",
        rules=(
            RegressionRule(
                rule_id="deterministic-pass-to-fail",
                kind=RegressionRuleKind.DETERMINISTIC_PASS_TO_FAIL,
            ),
        ),
    )
    aggregation_policy = AggregationPolicy(
        policy_id="reference.aggregate",
        version="1.0",
        score_method=AggregationMethod.MEAN,
        metric_method=AggregationMethod.MEAN,
        minimum_pass_rate=1.0,
        fail_on_error=True,
        require_equal_sample_count=True,
    )
    service = EvaluationService(
        repository=repository,
        runner=runner,
        suites=(suite,),
        policies=(policy,),
        aggregation_policies=(aggregation_policy,),
    )
    return service, executor, repository


def _control_plane(service: EvaluationService) -> tuple[ControlPlane, ControlPlaneHTTP]:
    repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
    )
    control_plane = ControlPlane(
        kernel=kernel,
        events=repository,
        resource_services=evaluation_resource_services(service),
        command_handlers=evaluation_command_handlers(service),
    )
    return control_plane, ControlPlaneHTTP(control_plane)


def _headers(key: str | None = None) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "X-Request-Id": "request-evaluation",
        "X-Correlation-Id": "correlation-evaluation",
        "X-Principal-Ref": "user:test",
        "X-Owner-Type": "user",
        "X-Owner-Id": "test",
    }
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


def _snapshot_payload(commit: str) -> dict[str, object]:
    return {
        "platform_version": "0.1.0",
        "platform_commit": commit,
        "references": [
            {
                "kind": "executor",
                "ref_id": "reference",
                "version": "1.0",
            }
        ],
        "environment": [{"key": "mode", "value": "test"}],
    }


def test_evaluation_service_runs_and_compares_configured_suite() -> None:
    async def scenario() -> None:
        service, executor, _ = _evaluation_stack()
        suite_ref = evaluation_suite_ref(service.list_suites()[0])
        policy = service.get_policy("reference.pr@1.0")

        baseline = await service.run_suite(
            suite_ref=suite_ref,
            snapshot=ConfigurationSnapshot(platform_version="0.1.0", platform_commit="baseline"),
        )
        assert baseline.results[0].deterministic_pass is True

        executor.status = "bad"
        current = await service.run_suite(
            suite_ref=suite_ref,
            snapshot=ConfigurationSnapshot(platform_version="0.1.0", platform_commit="current"),
        )
        comparison = service.compare_runs(
            current_run_id=current.run.run_id,
            baseline_run_id=baseline.run.run_id,
            regression_policy_ref_value=regression_policy_ref(policy),
        )
        assert len(comparison.regressions) == 1
        detail = service.get_run_detail(current.run.run_id)
        assert detail.comparison == comparison
        assert detail.results[0].deterministic_pass is False

    asyncio.run(scenario())


def test_control_plane_exposes_evaluation_resources_run_and_compare_commands() -> None:
    async def scenario() -> None:
        service, executor, _ = _evaluation_stack()
        control_plane, http = _control_plane(service)
        suite_ref = evaluation_suite_ref(service.list_suites()[0])

        assert EVALUATION_RUN_COLLECTION in control_plane.registered_collections
        assert EVALUATION_SUITE_COLLECTION in control_plane.registered_collections
        assert "evaluation.run" in control_plane.registered_commands
        assert "evaluation.compare" in control_plane.registered_commands

        manifest = await http.handle(HTTPRequest(method="GET", path="/api/v1"))
        assert manifest.status == 200
        assert isinstance(manifest.body, dict)
        assert EVALUATION_SUITE_COLLECTION in manifest.body["resources"]
        assert EVALUATION_RUN_COLLECTION in manifest.body["resources"]

        openapi = await http.handle(HTTPRequest(method="GET", path="/api/v1/openapi.json"))
        assert openapi.status == 200
        assert isinstance(openapi.body, dict)
        paths = openapi.body["paths"]
        assert isinstance(paths, dict)
        assert f"/api/v1/{EVALUATION_SUITE_COLLECTION}" in paths
        assert f"/api/v1/{EVALUATION_RUN_COLLECTION}" in paths
        assert "/api/v1/commands/{command}" in paths

        suites = await http.handle(
            HTTPRequest(
                method="GET",
                path=f"/api/v1/{EVALUATION_SUITE_COLLECTION}",
                headers=_headers(),
            )
        )
        assert suites.status == 200
        assert isinstance(suites.body, dict)
        assert suites.body["total"] == 1

        baseline_response = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/evaluation.run",
                headers=_headers("eval-baseline"),
                body={
                    "resource_ref": suite_ref,
                    "snapshot": _snapshot_payload("baseline"),
                },
            )
        )
        assert baseline_response.status == 200
        assert isinstance(baseline_response.body, dict)
        assert baseline_response.body["type"] == "evaluation-run"
        baseline_run_id = baseline_response.body["id"]
        assert isinstance(baseline_run_id, str)
        baseline_results = baseline_response.body["results"]
        assert isinstance(baseline_results, list)
        assert baseline_results[0]["deterministic_pass"] is True

        executor.status = "bad"
        current_response = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/evaluation.run",
                headers=_headers("eval-current"),
                body={
                    "resource_ref": suite_ref,
                    "snapshot": _snapshot_payload("current"),
                },
            )
        )
        assert current_response.status == 200
        assert isinstance(current_response.body, dict)
        current_run_id = current_response.body["id"]
        assert isinstance(current_run_id, str)

        comparison = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/evaluation.compare",
                headers=_headers("eval-compare"),
                body={
                    "resource_ref": current_run_id,
                    "baseline_run_id": baseline_run_id,
                    "regression_policy_ref": "reference.pr@1.0",
                },
            )
        )
        assert comparison.status == 200
        assert isinstance(comparison.body, dict)
        assert comparison.body["type"] == "evaluation-comparison"
        assert comparison.body["regression_count"] == 1

        loaded = await http.handle(
            HTTPRequest(
                method="GET",
                path=f"/api/v1/{EVALUATION_RUN_COLLECTION}/{current_run_id}",
                headers=_headers(),
            )
        )
        assert loaded.status == 200
        assert isinstance(loaded.body, dict)
        assert loaded.body["comparison"] == comparison.body

    asyncio.run(scenario())


def test_control_plane_repeated_runs_require_and_expose_exact_aggregation_policy() -> None:
    async def scenario() -> None:
        service, executor, _ = _evaluation_stack()
        _, http = _control_plane(service)
        suite_ref = evaluation_suite_ref(service.list_suites()[0])
        aggregation = service.get_aggregation_policy("reference.aggregate@1.0")
        aggregation_ref = aggregation_policy_ref(aggregation)

        baseline_response = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/evaluation.run",
                headers=_headers("eval-repeated-baseline"),
                body={
                    "resource_ref": suite_ref,
                    "snapshot": _snapshot_payload("repeated-baseline"),
                    "repetitions": 2,
                    "aggregation_policy_ref": aggregation_ref,
                },
            )
        )
        assert baseline_response.status == 200
        assert isinstance(baseline_response.body, dict)
        baseline_run_id = baseline_response.body["id"]
        assert isinstance(baseline_run_id, str)
        baseline_aggregates = baseline_response.body["aggregates"]
        assert isinstance(baseline_aggregates, list)
        assert len(baseline_aggregates) == 1
        assert baseline_aggregates[0]["aggregation_policy_id"] == aggregation.policy_id
        assert baseline_aggregates[0]["aggregation_policy_version"] == aggregation.version
        assert baseline_aggregates[0]["sample_count"] == 2
        assert baseline_aggregates[0]["pass_rate"] == 1.0

        executor.status = "bad"
        current_response = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/evaluation.run",
                headers=_headers("eval-repeated-current"),
                body={
                    "resource_ref": suite_ref,
                    "snapshot": _snapshot_payload("repeated-current"),
                    "repetitions": 2,
                    "aggregation_policy_ref": aggregation_ref,
                },
            )
        )
        assert current_response.status == 200
        assert isinstance(current_response.body, dict)
        current_run_id = current_response.body["id"]
        assert isinstance(current_run_id, str)
        current_aggregates = current_response.body["aggregates"]
        assert isinstance(current_aggregates, list)
        assert current_aggregates[0]["sample_count"] == 2
        assert current_aggregates[0]["pass_rate"] == 0.0

        comparison = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/evaluation.compare",
                headers=_headers("eval-repeated-compare"),
                body={
                    "resource_ref": current_run_id,
                    "baseline_run_id": baseline_run_id,
                    "regression_policy_ref": "reference.pr@1.0",
                    "aggregation_policy_ref": aggregation_ref,
                },
            )
        )
        assert comparison.status == 200
        assert isinstance(comparison.body, dict)
        assert comparison.body["regression_count"] == 1

        loaded = await http.handle(
            HTTPRequest(
                method="GET",
                path=f"/api/v1/{EVALUATION_RUN_COLLECTION}/{current_run_id}",
                headers=_headers(),
            )
        )
        assert loaded.status == 200
        assert isinstance(loaded.body, dict)
        snapshot = loaded.body["snapshot"]
        assert isinstance(snapshot, dict)
        references = snapshot["references"]
        assert isinstance(references, list)
        aggregation_refs = [
            reference
            for reference in references
            if isinstance(reference, dict) and reference.get("kind") == "aggregation_policy"
        ]
        assert aggregation_refs == [
            {
                "kind": "aggregation_policy",
                "ref_id": aggregation.policy_id,
                "version": aggregation.version,
                "revision": None,
            }
        ]
        assert loaded.body["comparison"] == comparison.body

    asyncio.run(scenario())


def test_control_plane_run_history_paginates_beyond_repository_default_limit() -> None:
    async def scenario() -> None:
        service, _, repository = _evaluation_stack()
        _, http = _control_plane(service)
        snapshot = ConfigurationSnapshot(platform_version="0.1.0")
        for _ in range(105):
            repository.save_run(
                EvaluationRun(
                    suite_id="history.pagination",
                    suite_version="1",
                    snapshot=snapshot,
                )
            )

        first = await http.handle(
            HTTPRequest(
                method="GET",
                path=f"/api/v1/{EVALUATION_RUN_COLLECTION}",
                headers=_headers(),
                query={"limit": "100"},
            )
        )
        assert first.status == 200
        assert isinstance(first.body, dict)
        assert first.body["total"] == 105
        first_items = first.body["items"]
        assert isinstance(first_items, list)
        assert len(first_items) == 100
        cursor = first.body["next_cursor"]
        assert isinstance(cursor, str)

        second = await http.handle(
            HTTPRequest(
                method="GET",
                path=f"/api/v1/{EVALUATION_RUN_COLLECTION}",
                headers=_headers(),
                query={"limit": "100", "cursor": cursor},
            )
        )
        assert second.status == 200
        assert isinstance(second.body, dict)
        assert second.body["total"] == 105
        second_items = second.body["items"]
        assert isinstance(second_items, list)
        assert len(second_items) == 5
        assert second.body["next_cursor"] is None

    asyncio.run(scenario())
