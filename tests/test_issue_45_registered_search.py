from __future__ import annotations

import asyncio

from ai_multi_agent_platform.contracts.types import AuthorizationDecision, AuthorizationRequest
from ai_multi_agent_platform.control_plane import (
    ControlPlane,
    ControlPlaneHTTP,
    HTTPRequest,
    InMemoryResourceService,
)
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.search import document_from_resource
from ai_multi_agent_platform.testing import (
    FakeAuthorizationProvider,
    FakeLifecycleBackend,
    FakeOrchestrator,
)


class ExtensionFilteringAuthorization(FakeAuthorizationProvider):
    def __init__(self, denied_project_id: str) -> None:
        super().__init__()
        self.denied_project_id = denied_project_id

    async def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision:
        self.calls.append(request)
        if request.action == "agent-team:list":
            return AuthorizationDecision(allowed=False, reason="team-hidden")
        if request.action == "agent:list" and request.context.project_id == self.denied_project_id:
            return AuthorizationDecision(allowed=False, reason="project-hidden")
        return AuthorizationDecision(allowed=True, reason="visible")


def _control_plane(
    authorization: FakeAuthorizationProvider,
    *,
    resources: dict[str, InMemoryResourceService],
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
        resource_services=resources,
    )
    return control_plane, ControlPlaneHTTP(control_plane)


def test_registered_agent_shape_maps_to_canonical_search_document() -> None:
    project_id = new_id("project")
    workspace_id = new_id("workspace")
    document = document_from_resource(
        {
            "id": "agent_researcher",
            "type": "agent",
            "project_id": project_id,
            "workspace_id": workspace_id,
            "owner_ref": {"type": "user", "id": "owner-1"},
            "current_revision": 2,
            "updated_at": "2026-09-03T16:00:00+00:00",
            "labels": ["research", "production"],
            "revision": {
                "profile": {
                    "name": "Research Agent",
                    "role": "researcher",
                    "description": "Finds canonical resources safely.",
                }
            },
        },
        collection="agents",
    )

    assert document.resource_type == "agent"
    assert document.resource_id == "agent_researcher"
    assert document.title == "Research Agent"
    assert document.summary == "Finds canonical resources safely."
    assert document.project_id == project_id
    assert document.workspace_id == workspace_id
    assert document.owner_type == "user"
    assert document.owner_id == "owner-1"
    assert document.version == "2"
    assert document.tags == ("research", "production")
    assert "researcher" in document.keywords
    assert document.canonical_ref == "/api/v1/agents/agent_researcher"
    assert document.provenance == {
        "indexed_from": "canonical-control-plane",
        "collection": "agents",
    }


def test_registered_resources_are_searchable_with_canonical_collection_authorization() -> None:
    async def scenario() -> None:
        visible_project_id = new_id("project")
        hidden_project_id = new_id("project")
        agent_id = "agent_search_visible"
        hidden_agent_id = "agent_search_project_hidden"
        team_id = "agent_team_search_hidden"
        authorization = ExtensionFilteringAuthorization(hidden_project_id)
        control_plane, http = _control_plane(
            authorization,
            resources={
                "agents": InMemoryResourceService(
                    (
                        {
                            "id": agent_id,
                            "type": "agent",
                            "project_id": visible_project_id,
                            "owner_ref": {"type": "user", "id": "owner-1"},
                            "current_revision": 3,
                            "updated_at": "2026-09-03T16:10:00+00:00",
                            "revision": {
                                "profile": {
                                    "name": "Search Research Agent",
                                    "role": "researcher",
                                    "description": "Searches authorized canonical resources.",
                                }
                            },
                        },
                        {
                            "id": hidden_agent_id,
                            "type": "agent",
                            "project_id": hidden_project_id,
                            "owner_ref": {"type": "user", "id": "owner-2"},
                            "current_revision": 1,
                            "updated_at": "2026-09-03T16:10:30+00:00",
                            "revision": {
                                "profile": {
                                    "name": "Search Hidden Project Agent",
                                    "role": "researcher",
                                    "description": "Must not leak across project scope.",
                                }
                            },
                        },
                    )
                ),
                "agent-teams": InMemoryResourceService(
                    (
                        {
                            "id": team_id,
                            "type": "agent_team",
                            "project_id": visible_project_id,
                            "owner_ref": {"type": "team", "id": "secret-team"},
                            "current_revision": 1,
                            "updated_at": "2026-09-03T16:11:00+00:00",
                            "revision": {
                                "profile": {
                                    "name": "Search Secret Team",
                                    "description": "Must not leak through Search.",
                                }
                            },
                        },
                    )
                ),
            },
        )

        response = await http.handle(
            HTTPRequest(method="GET", path="/api/v1/search", query={"q": "Search"})
        )
        assert response.status == 200
        assert isinstance(response.body, dict)
        items = response.body["items"]
        assert isinstance(items, list)
        assert response.body["total"] == 1
        assert len(items) == 1
        result = items[0]
        assert isinstance(result, dict)
        assert result["resource_type"] == "agent"
        assert result["resource_id"] == agent_id
        assert result["title"] == "Search Research Agent"
        assert result["canonical_ref"] == f"/api/v1/agents/{agent_id}"
        assert result["access"] == "authorized"
        serialized = repr(response.body)
        assert hidden_agent_id not in serialized
        assert "Search Hidden Project Agent" not in serialized
        assert team_id not in serialized
        assert "Search Secret Team" not in serialized
        assert any(call.action == "agent:list" for call in authorization.calls)
        assert any(
            call.action == "agent:list" and call.context.project_id == hidden_project_id
            for call in authorization.calls
        )
        assert any(call.action == "agent-team:list" for call in authorization.calls)

        exact = await http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/search",
                query={"id": agent_id, "type": "agent"},
            )
        )
        assert exact.status == 200
        assert isinstance(exact.body, dict)
        assert exact.body["total"] == 1

        hidden_exact = await http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/search",
                query={"id": hidden_agent_id, "type": "agent"},
            )
        )
        assert hidden_exact.status == 200
        assert isinstance(hidden_exact.body, dict)
        assert hidden_exact.body["total"] == 0
        assert hidden_agent_id not in repr(hidden_exact.body)

        rebuilt = await control_plane.rebuild_search_index()
        assert rebuilt == 3

    asyncio.run(scenario())


def test_registered_resource_private_fields_cannot_enter_search_index() -> None:
    async def scenario() -> None:
        _, http = _control_plane(
            FakeAuthorizationProvider(),
            resources={
                "agents": InMemoryResourceService(
                    (
                        {
                            "id": "agent_private_leak",
                            "type": "agent",
                            "backend_ref": "secret-backend-id",
                        },
                    )
                )
            },
        )

        response = await http.handle(
            HTTPRequest(method="GET", path="/api/v1/search", query={"q": "agent"})
        )
        assert response.status == 500
        assert isinstance(response.body, dict)
        assert response.body["code"] == "contract_violation"
        assert "secret-backend-id" not in repr(response.body)

    asyncio.run(scenario())
