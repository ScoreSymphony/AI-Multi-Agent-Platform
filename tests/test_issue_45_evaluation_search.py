from __future__ import annotations

import asyncio
from datetime import timedelta

from ai_multi_agent_platform.contracts.types import AuthorizationDecision, AuthorizationRequest
from ai_multi_agent_platform.control_plane import ControlPlane, ControlPlaneHTTP, HTTPRequest
from ai_multi_agent_platform.control_plane.evaluation_contract import evaluation_resource_services
from ai_multi_agent_platform.evaluation import (
    ComparisonOperator,
    ConfigurationSnapshot,
    DeterministicAssertion,
    DeterministicAssertionEvaluator,
    EvaluationAttempt,
    EvaluationCase,
    EvaluationExecutionContext,
    EvaluationObservation,
    EvaluationRunner,
    EvaluationSuite,
    InMemoryEvaluationRepository,
    SnapshotValue,
)
from ai_multi_agent_platform.evaluation.service import EvaluationService, evaluation_suite_ref
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import (
    FakeAuthorizationProvider,
    FakeLifecycleBackend,
    FakeOrchestrator,
)

SUITE_FIXTURE_SECRET = "fixture-secret-must-not-be-searchable"
SNAPSHOT_SECRET = "snapshot-secret-must-not-be-searchable"


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


class EvaluationSearchAuthorization(FakeAuthorizationProvider):
    def __init__(self, denied_actions: frozenset[str]) -> None:
        super().__init__()
        self.denied_actions = denied_actions

    async def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision:
        self.calls.append(request)
        if request.action in self.denied_actions:
            return AuthorizationDecision(allowed=False, reason="evaluation-hidden")
        return AuthorizationDecision(allowed=True, reason="evaluation-visible")


def _evaluation_service() -> EvaluationService:
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
        description="Deterministic lifecycle regression coverage",
        tags=("critical", "lifecycle"),
        cases=(
            EvaluationCase(
                case_id="status-check",
                name="Status check",
                version="1.0",
                fixtures=(SUITE_FIXTURE_SECRET,),
                assertions=(
                    DeterministicAssertion(
                        assertion_id="status-ok",
                        path="status",
                        operator=ComparisonOperator.EQ,
                        expected="ok",
                    ),
                ),
            ),
        ),
    )
    return EvaluationService(repository=repository, runner=runner, suites=(suite,))


def _stack(
    service: EvaluationService,
    authorization: FakeAuthorizationProvider | None = None,
) -> tuple[ControlPlane, ControlPlaneHTTP]:
    repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
    )
    control_plane = ControlPlane(
        kernel=kernel,
        events=repository,
        authorization=authorization,
        resource_services=evaluation_resource_services(service),
    )
    return control_plane, ControlPlaneHTTP(control_plane)


async def _search(http: ControlPlaneHTTP, **query: str) -> dict[str, object]:
    response = await http.handle(HTTPRequest(method="GET", path="/api/v1/search", query=query))
    assert response.status == 200, response.body
    assert isinstance(response.body, dict)
    return response.body


def _items(page: dict[str, object]) -> list[dict[str, object]]:
    items = page["items"]
    assert isinstance(items, list)
    assert all(isinstance(item, dict) for item in items)
    return items


def test_evaluation_suites_and_runs_use_global_search_with_safe_flat_metadata() -> None:
    async def scenario() -> None:
        service = _evaluation_service()
        _, http = _stack(service)
        suite = service.list_suites()[0]
        suite_ref = evaluation_suite_ref(suite)

        baseline = await service.run_suite(
            suite_ref=suite_ref,
            snapshot=ConfigurationSnapshot(platform_version="0.1.0", platform_commit="baseline"),
        )
        current = await service.run_suite(
            suite_ref=suite_ref,
            snapshot=ConfigurationSnapshot(
                platform_version="0.1.0",
                platform_commit="current",
                environment=(SnapshotValue(key="private-mode", value=SNAPSHOT_SECRET),),
            ),
            baseline_run_id=baseline.run.run_id,
        )

        exact_suite = await _search(http, type="evaluation-suite", id=suite_ref)
        assert exact_suite["total"] == 1
        suite_item = _items(exact_suite)[0]
        assert suite_item["title"] == "Reference lifecycle"
        assert suite_item["summary"] == "Deterministic lifecycle regression coverage"
        assert suite_item["tags"] == ["critical", "lifecycle"]
        assert suite_item["version"] == "1.0"
        assert suite_item["canonical_ref"] == f"/api/v1/evaluation-suites/{suite_ref}"

        by_suite_id = await _search(http, type="evaluation-suite", q="reference.lifecycle")
        assert by_suite_id["total"] == 1
        by_tag = await _search(http, type="evaluation-suite", tag="critical")
        assert by_tag["total"] == 1

        exact_run = await _search(http, type="evaluation-run", id=current.run.run_id)
        assert exact_run["total"] == 1
        run_item = _items(exact_run)[0]
        assert run_item["status"] == "completed"
        assert run_item["title"] == "Evaluation run for reference.lifecycle 1.0"
        assert run_item["canonical_ref"] == f"/api/v1/evaluation-runs/{current.run.run_id}"
        assert current.run.completed_at is not None
        assert run_item["updated_at"] == current.run.completed_at.isoformat()

        by_suite = await _search(http, type="evaluation-run", q="reference.lifecycle")
        assert by_suite["total"] == 2
        by_suite_version = await _search(http, type="evaluation-run", q="1.0")
        assert by_suite_version["total"] == 2
        by_baseline = await _search(
            http,
            type="evaluation-run",
            q=baseline.run.run_id,
        )
        assert by_baseline["total"] == 1
        assert _items(by_baseline)[0]["resource_id"] == current.run.run_id
        by_status = await _search(http, type="evaluation-run", status="completed")
        assert by_status["total"] == 2

        after = baseline.run.started_at - timedelta(seconds=1)
        before = current.run.completed_at + timedelta(seconds=1)
        by_time = await _search(
            http,
            type="evaluation-run",
            updated_after=after.isoformat(),
            updated_before=before.isoformat(),
        )
        assert by_time["total"] == 2

        fixture_leak = await _search(http, type="evaluation-suite", q=SUITE_FIXTURE_SECRET)
        assert fixture_leak["total"] == 0
        snapshot_leak = await _search(http, type="evaluation-run", q=SNAPSHOT_SECRET)
        assert snapshot_leak["total"] == 0
        serialized = repr(exact_suite) + repr(exact_run)
        assert SUITE_FIXTURE_SECRET not in serialized
        assert SNAPSHOT_SECRET not in serialized
        assert "private-mode" not in serialized

    asyncio.run(scenario())


def test_evaluation_search_reuses_registered_collection_authorization_without_disclosure() -> None:
    async def scenario() -> None:
        service = _evaluation_service()
        suite_ref = evaluation_suite_ref(service.list_suites()[0])
        run = await service.run_suite(
            suite_ref=suite_ref,
            snapshot=ConfigurationSnapshot(platform_version="0.1.0"),
        )
        authorization = EvaluationSearchAuthorization(
            frozenset({"evaluation-suite:list", "evaluation-run:list"})
        )
        _, http = _stack(service, authorization)

        hidden_suites = await _search(http, type="evaluation-suite")
        assert hidden_suites["total"] == 0
        assert _items(hidden_suites) == []
        hidden_suite_exact = await _search(http, type="evaluation-suite", id=suite_ref)
        assert hidden_suite_exact["total"] == 0
        assert suite_ref not in repr(hidden_suite_exact)

        hidden_runs = await _search(http, type="evaluation-run")
        assert hidden_runs["total"] == 0
        assert _items(hidden_runs) == []
        hidden_run_exact = await _search(http, type="evaluation-run", id=run.run.run_id)
        assert hidden_run_exact["total"] == 0
        assert run.run.run_id not in repr(hidden_run_exact)

        assert any(call.action == "evaluation-suite:list" for call in authorization.calls)
        assert any(call.action == "evaluation-run:list" for call in authorization.calls)

    asyncio.run(scenario())
