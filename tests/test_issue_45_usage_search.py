from __future__ import annotations

import asyncio
import json

from ai_multi_agent_platform.accounting import (
    AccountingService,
    AggregationMode,
    InMemoryUsageStore,
    MeasurementQuality,
    UsageBudget,
    UsageRecord,
    UsageScope,
    accounting_resource_services,
)
from ai_multi_agent_platform.contracts.types import AuthorizationDecision, AuthorizationRequest
from ai_multi_agent_platform.control_plane import ControlPlane, ControlPlaneHTTP, HTTPRequest
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import (
    FakeAuthorizationProvider,
    FakeLifecycleBackend,
    FakeOrchestrator,
)


class UsageSearchAuthorization(FakeAuthorizationProvider):
    async def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision:
        self.calls.append(request)
        if request.action in {"usage-aggregate:list", "usage-budget:list"}:
            if request.context.owner_id == "bob":
                return AuthorizationDecision(allowed=False, reason="hidden-owner")
        return AuthorizationDecision(allowed=True, reason="visible-owner")


def _headers() -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "X-Principal-Ref": "user:alice",
        "X-Owner-Type": "user",
        "X-Owner-Id": "alice",
    }


def _stack(
    accounting: AccountingService,
    authorization: UsageSearchAuthorization,
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
        resource_services=accounting_resource_services(accounting),
    )
    return control_plane, ControlPlaneHTTP(control_plane)


async def _search(http: ControlPlaneHTTP, **query: str) -> dict[str, object]:
    response = await http.handle(
        HTTPRequest(
            method="GET",
            path="/api/v1/search",
            headers=_headers(),
            query=query,
        )
    )
    assert response.status == 200, response.body
    assert isinstance(response.body, dict)
    return response.body


def _items(page: dict[str, object]) -> list[dict[str, object]]:
    items = page["items"]
    assert isinstance(items, list)
    assert all(isinstance(item, dict) for item in items)
    return items


def test_usage_aggregates_and_budgets_are_searchable_without_indexing_raw_records() -> None:
    async def scenario() -> None:
        alice_project = new_id("project")
        accounting = AccountingService(InMemoryUsageStore())
        accounting.record(
            UsageRecord(
                metric_type="storage.file.bytes.current",
                unit="bytes",
                quality=MeasurementQuality.REPORTED,
                source="file-provider-private-source",
                provider="provider-private-account",
                quantity=42.0,
                aggregation_mode=AggregationMode.LATEST,
                scope=UsageScope(
                    owner_type="user",
                    owner_id="alice",
                    project_id=alice_project,
                ),
                provenance={"private_invoice_ref": "invoice-secret-98765"},
            )
        )
        budget = UsageBudget(
            metric_type="storage.file.bytes.current",
            unit="bytes",
            scope_type="project",
            scope_id=alice_project,
            limit=12345.6789,
            owner_type="user",
            owner_id="alice",
        )
        accounting.put_budget(budget)

        authorization = UsageSearchAuthorization()
        control_plane, http = _stack(accounting, authorization)

        aggregate_page = await _search(
            http,
            type="usage-aggregate",
            q="storage.file.bytes.current",
            project_id=alice_project,
        )
        assert aggregate_page["total"] == 1
        aggregate = _items(aggregate_page)[0]
        assert aggregate["resource_type"] == "usage-aggregate"
        assert aggregate["title"] == "storage.file.bytes.current usage (bytes)"
        assert aggregate["project_id"] == alice_project
        aggregate_id = aggregate["resource_id"]
        assert isinstance(aggregate_id, str)
        assert aggregate["canonical_ref"] == f"/api/v1/usage-aggregates/{aggregate_id}"
        assert aggregate["provenance"] == {
            "indexed_from": "canonical-control-plane",
            "collection": "usage-aggregates",
        }

        budget_page = await _search(
            http,
            type="usage-budget",
            q="soft",
            project_id=alice_project,
        )
        assert budget_page["total"] == 1
        budget_result = _items(budget_page)[0]
        assert budget_result["resource_id"] == budget.id
        assert budget_result["title"] == (
            f"storage.file.bytes.current budget for project {alice_project}"
        )
        assert budget_result["project_id"] == alice_project
        assert budget_result["canonical_ref"] == f"/api/v1/usage-budgets/{budget.id}"

        exact_budget = await _search(http, type="usage-budget", id=budget.id)
        assert exact_budget["total"] == 1

        raw_records = await _search(http, type="usage-record", q="storage.file.bytes.current")
        assert raw_records["total"] == 0

        for private_value in (
            "42.0",
            "12345.6789",
            "file-provider-private-source",
            "provider-private-account",
            "invoice-secret-98765",
        ):
            page = await _search(http, q=private_value)
            assert page["total"] == 0, (private_value, page)

        rebuilt = await control_plane.rebuild_search_index()
        assert rebuilt == 2

        actions = {call.action for call in authorization.calls}
        assert "usage-aggregate:list" in actions
        assert "usage-budget:list" in actions
        assert "usage-record:list" not in actions

    asyncio.run(scenario())


def test_usage_search_enumerates_all_owners_but_hides_unauthorized_owner_existence() -> None:
    async def scenario() -> None:
        alice_project = new_id("project")
        bob_project = new_id("project")
        accounting = AccountingService(InMemoryUsageStore())
        accounting.record(
            UsageRecord(
                metric_type="alice.storage.bytes",
                unit="bytes",
                quality=MeasurementQuality.MEASURED,
                source="local",
                quantity=10.0,
                aggregation_mode=AggregationMode.LATEST,
                scope=UsageScope(
                    owner_type="user",
                    owner_id="alice",
                    project_id=alice_project,
                ),
            )
        )
        accounting.record(
            UsageRecord(
                metric_type="bob.hidden.tokens",
                unit="tokens",
                quality=MeasurementQuality.REPORTED,
                source="hidden-provider",
                quantity=9000.0,
                aggregation_mode=AggregationMode.LATEST,
                scope=UsageScope(
                    owner_type="user",
                    owner_id="bob",
                    project_id=bob_project,
                ),
            )
        )
        alice_budget = UsageBudget(
            metric_type="alice.storage.bytes",
            unit="bytes",
            scope_type="project",
            scope_id=alice_project,
            limit=100.0,
            owner_type="user",
            owner_id="alice",
        )
        bob_budget = UsageBudget(
            metric_type="bob.hidden.tokens",
            unit="tokens",
            scope_type="project",
            scope_id=bob_project,
            limit=10000.0,
            owner_type="user",
            owner_id="bob",
        )
        accounting.put_budget(alice_budget)
        accounting.put_budget(bob_budget)

        authorization = UsageSearchAuthorization()
        control_plane, http = _stack(accounting, authorization)

        rebuilt = await control_plane.rebuild_search_index()
        assert rebuilt == 4

        alice_results = await _search(http, q="alice.storage.bytes")
        assert alice_results["total"] == 2
        assert {item["resource_type"] for item in _items(alice_results)} == {
            "usage-aggregate",
            "usage-budget",
        }

        hidden_metric = await _search(http, q="bob.hidden.tokens")
        hidden_project = await _search(http, project_id=bob_project)
        hidden_budget_exact = await _search(http, type="usage-budget", id=bob_budget.id)

        assert hidden_metric["total"] == 0
        assert hidden_project["total"] == 0
        assert hidden_budget_exact["total"] == 0

        serialized = json.dumps(
            {
                "metric": hidden_metric,
                "project": hidden_project,
                "budget": hidden_budget_exact,
            },
            sort_keys=True,
        )
        assert "bob.hidden.tokens" not in serialized
        assert bob_project not in serialized
        assert bob_budget.id not in serialized
        assert "bob" not in serialized

        denied_calls = [
            call
            for call in authorization.calls
            if call.action in {"usage-aggregate:list", "usage-budget:list"}
            and call.context.owner_id == "bob"
        ]
        assert denied_calls
        assert any(call.context.project_id == bob_project for call in denied_calls)

    asyncio.run(scenario())
