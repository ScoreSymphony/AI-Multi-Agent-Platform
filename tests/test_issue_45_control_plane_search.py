from __future__ import annotations

import asyncio

from control_plane_contract_helpers import api_headers

from ai_multi_agent_platform.contracts.types import AuthorizationDecision, AuthorizationRequest
from ai_multi_agent_platform.control_plane import (
    ControlPlane,
    ControlPlaneHTTP,
    HTTPRequest,
    build_openapi,
)
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import (
    FakeAuthorizationProvider,
    FakeLifecycleBackend,
    FakeOrchestrator,
)


class ProjectFilteringAuthorization(FakeAuthorizationProvider):
    def __init__(self) -> None:
        super().__init__()
        self.denied_project_id: str | None = None

    async def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision:
        self.calls.append(request)
        if (
            self.denied_project_id is not None
            and request.action.endswith(":list")
            and request.context.project_id == self.denied_project_id
        ):
            return AuthorizationDecision(allowed=False, reason="project-hidden")
        return AuthorizationDecision(allowed=True, reason="project-visible")


def _stack(
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
    )
    return control_plane, ControlPlaneHTTP(control_plane)


async def _create_project(http: ControlPlaneHTTP, key: str, name: str) -> str:
    response = await http.handle(
        HTTPRequest(
            method="POST",
            path="/api/v1/projects",
            headers=api_headers(idempotency_key=key),
            body={"name": name, "owner_type": "user", "owner_id": "test"},
        )
    )
    assert response.status == 201
    assert isinstance(response.body, dict)
    project_id = response.body["id"]
    assert isinstance(project_id, str)
    return project_id


async def _create_task(
    http: ControlPlaneHTTP,
    key: str,
    title: str,
    project_id: str,
) -> str:
    response = await http.handle(
        HTTPRequest(
            method="POST",
            path="/api/v1/tasks",
            headers=api_headers(idempotency_key=key),
            body={
                "title": title,
                "objective": f"Objective for {title}",
                "owner_type": "user",
                "owner_id": "test",
                "project_id": project_id,
            },
        )
    )
    assert response.status == 201
    assert isinstance(response.body, dict)
    task_id = response.body["id"]
    assert isinstance(task_id, str)
    return task_id


def test_global_search_exact_keyword_project_filter_and_run_lookup() -> None:
    async def scenario() -> None:
        control_plane, http = _stack(FakeAuthorizationProvider())
        project_id = await _create_project(http, "search-project", "Search Platform")
        task_id = await _create_task(http, "search-task", "Build global search", project_id)

        started = await http.handle(
            HTTPRequest(
                method="POST",
                path=f"/api/v1/tasks/{task_id}:start",
                headers=api_headers(idempotency_key="search-run"),
            )
        )
        assert started.status == 200
        assert isinstance(started.body, dict)
        run_id = started.body["id"]
        assert isinstance(run_id, str)

        keyword = await http.handle(
            HTTPRequest(method="GET", path="/api/v1/search", query={"q": "search"})
        )
        assert keyword.status == 200
        assert isinstance(keyword.body, dict)
        assert keyword.body["total"] >= 2
        items = keyword.body["items"]
        assert isinstance(items, list)
        identities = {
            (item["resource_type"], item["resource_id"])
            for item in items
            if isinstance(item, dict)
        }
        assert ("project", project_id) in identities
        assert ("task", task_id) in identities

        for resource_type, resource_id in (
            ("project", project_id),
            ("task", task_id),
            ("run", run_id),
        ):
            exact = await http.handle(
                HTTPRequest(
                    method="GET",
                    path="/api/v1/search",
                    query={"id": resource_id, "type": resource_type},
                )
            )
            assert exact.status == 200
            assert isinstance(exact.body, dict)
            assert exact.body["total"] == 1
            exact_items = exact.body["items"]
            assert isinstance(exact_items, list)
            assert exact_items[0]["resource_id"] == resource_id
            assert exact_items[0]["resource_type"] == resource_type
            assert exact_items[0]["access"] == "authorized"

        scoped = await http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/search",
                query={"project_id": project_id, "type": "task,run"},
            )
        )
        assert scoped.status == 200
        assert isinstance(scoped.body, dict)
        scoped_items = scoped.body["items"]
        assert isinstance(scoped_items, list)
        assert {item["resource_type"] for item in scoped_items if isinstance(item, dict)} == {
            "task",
            "run",
        }

        rebuilt = await control_plane.rebuild_search_index()
        assert rebuilt >= 3

    asyncio.run(scenario())


def test_search_authorization_filters_items_counts_and_snippets() -> None:
    async def scenario() -> None:
        authorization = ProjectFilteringAuthorization()
        _, http = _stack(authorization)
        visible_project = await _create_project(http, "visible-project", "Search Visible")
        hidden_project = await _create_project(http, "hidden-project", "Search Secret Project")
        visible_task = await _create_task(
            http,
            "visible-task",
            "Search visible task",
            visible_project,
        )
        hidden_task = await _create_task(
            http,
            "hidden-task",
            "Search secret task",
            hidden_project,
        )
        authorization.denied_project_id = hidden_project

        response = await http.handle(
            HTTPRequest(method="GET", path="/api/v1/search", query={"q": "Search"})
        )

        assert response.status == 200
        assert isinstance(response.body, dict)
        items = response.body["items"]
        assert isinstance(items, list)
        ids = {item["resource_id"] for item in items if isinstance(item, dict)}
        assert visible_project in ids
        assert visible_task in ids
        assert hidden_project not in ids
        assert hidden_task not in ids
        assert response.body["total"] == len(items)
        serialized = repr(response.body)
        assert hidden_project not in serialized
        assert hidden_task not in serialized
        assert "Search Secret Project" not in serialized
        assert "Search secret task" not in serialized

    asyncio.run(scenario())


def test_search_rebuild_removes_stale_provider_state_and_semantic_degrades_cleanly() -> None:
    async def scenario() -> None:
        control_plane, http = _stack(FakeAuthorizationProvider())
        project_id = await _create_project(http, "canonical-project", "Canonical Search")

        from ai_multi_agent_platform.search import SearchDocument
        from ai_multi_agent_platform.contracts.types import OperationContext

        await control_plane.search_provider.upsert(
            SearchDocument(
                resource_type="project",
                resource_id="project_00000000-0000-0000-0000-000000000000",
                title="Stale search-only project",
                project_id="project_00000000-0000-0000-0000-000000000000",
            ),
            OperationContext(correlation_id="stale-search-test"),
        )

        stale = await http.handle(
            HTTPRequest(method="GET", path="/api/v1/search", query={"q": "Stale search-only"})
        )
        assert stale.status == 200
        assert isinstance(stale.body, dict)
        assert stale.body["total"] == 0

        canonical = await http.handle(
            HTTPRequest(method="GET", path="/api/v1/search", query={"id": project_id})
        )
        assert canonical.status == 200
        assert isinstance(canonical.body, dict)
        assert canonical.body["total"] == 1

        semantic = await http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/search",
                query={"q": "meaning", "mode": "semantic"},
            )
        )
        assert semantic.status == 400
        assert isinstance(semantic.body, dict)
        assert semantic.body["code"] == "unsupported_capability"

    asyncio.run(scenario())


def test_search_openapi_is_published() -> None:
    specification = build_openapi()
    assert "/api/v1/search" in specification["paths"]
    assert "SearchResult" in specification["components"]["schemas"]
    assert "SearchPage" in specification["components"]["schemas"]
