from __future__ import annotations

import asyncio
import json
from pathlib import Path

from ai_multi_agent_platform.contracts.types import (
    AuthorizationDecision,
    AuthorizationRequest,
    OperationContext,
)
from ai_multi_agent_platform.control_plane import ControlPlane, ControlPlaneHTTP, HTTPRequest
from ai_multi_agent_platform.control_plane.service import ScopeStore
from ai_multi_agent_platform.data import (
    DataAccessContext,
    DataProviderSet,
    LocalFileProvider,
    LocalKnowledgeProvider,
    LocalMemoryProvider,
)
from ai_multi_agent_platform.data.control_plane import data_resource_services
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import (
    FakeAuthorizationProvider,
    FakeLifecycleBackend,
    FakeOrchestrator,
)


class FileSearchAuthorization(FakeAuthorizationProvider):
    def __init__(self, denied_project_id: str | None = None) -> None:
        super().__init__()
        self.denied_project_id = denied_project_id

    async def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision:
        self.calls.append(request)
        if (
            self.denied_project_id is not None
            and request.action == "file:list"
            and request.context.project_id == self.denied_project_id
        ):
            return AuthorizationDecision(allowed=False, reason="file-project-hidden")
        return AuthorizationDecision(allowed=True, reason="file-search-visible")


def _data_context(*, project_id: str | None, owner_id: str) -> DataAccessContext:
    return DataAccessContext(
        operation=OperationContext(
            correlation_id="issue-45-file-search",
            owner_type="user",
            owner_id=owner_id,
            project_id=project_id,
        ),
        actor_ref=f"user:{owner_id}",
    )


def _providers(tmp_path: Path) -> DataProviderSet:
    return DataProviderSet(
        files=LocalFileProvider(tmp_path / "files", tmp_path / "files.sqlite3"),
        memory=LocalMemoryProvider(tmp_path / "memory.sqlite3"),
        knowledge=LocalKnowledgeProvider(tmp_path / "knowledge.sqlite3"),
    )


def _stack(
    providers: DataProviderSet,
    scopes: ScopeStore,
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
        scopes=scopes,
        authorization=authorization,
        resource_services=data_resource_services(
            providers,
            project_ids=lambda: tuple(project.id for project in scopes.list_projects()),
        ),
    )
    return control_plane, ControlPlaneHTTP(control_plane)


async def _search(http: ControlPlaneHTTP, **query: str) -> dict[str, object]:
    response = await http.handle(
        HTTPRequest(method="GET", path="/api/v1/search", query={"type": "file", **query})
    )
    assert response.status == 200, response.body
    assert isinstance(response.body, dict)
    return response.body


def _items(page: dict[str, object]) -> list[dict[str, object]]:
    items = page["items"]
    assert isinstance(items, list)
    assert all(isinstance(item, dict) for item in items)
    return items


def test_file_resource_service_and_search_use_canonical_file_metadata(tmp_path: Path) -> None:
    async def scenario() -> None:
        providers = _providers(tmp_path)
        scopes = ScopeStore()
        visible = scopes.create_project(
            key="file-visible-project",
            name="Visible files",
            owner_type="user",
            owner_id="visible-owner",
        )
        hidden = scopes.create_project(
            key="file-hidden-project",
            name="Hidden files",
            owner_type="user",
            owner_id="hidden-owner",
        )
        artifact_id = new_id("artifact")

        visible_record = await providers.files.create_file(
            b"visible file bytes",
            _data_context(project_id=visible.id, owner_id="visible-owner"),
            content_type="application/pdf",
            metadata={"private_file_secret": "needle-that-must-not-be-searchable"},
        )
        visible_record = await providers.files.link_artifact(
            visible_record.file_id,
            artifact_id,
            _data_context(project_id=visible.id, owner_id="visible-owner"),
        )
        hidden_record = await providers.files.create_file(
            b"hidden file bytes",
            _data_context(project_id=hidden.id, owner_id="hidden-owner"),
            content_type="text/plain",
        )

        authorization = FileSearchAuthorization(denied_project_id=hidden.id)
        control_plane, http = _stack(providers, scopes, authorization)

        direct = await http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/files",
                query={"filter[project_id]": visible.id},
            )
        )
        assert direct.status == 200, direct.body
        assert isinstance(direct.body, dict)
        direct_items = direct.body["items"]
        assert isinstance(direct_items, list)
        assert len(direct_items) == 1
        assert direct_items[0]["id"] == visible_record.file_id
        assert direct_items[0]["project_id"] == visible.id
        assert direct_items[0]["state"] == "ready"
        assert direct_items[0]["content_type"] == "application/pdf"
        assert direct_items[0]["artifact_ids"] == [artifact_id]

        exact = await _search(http, id=visible_record.file_id)
        assert exact["total"] == 1
        result = _items(exact)[0]
        assert result["resource_type"] == "file"
        assert result["resource_id"] == visible_record.file_id
        assert result["project_id"] == visible.id
        assert result["owner_type"] == "user"
        assert result["owner_id"] == "visible-owner"
        assert result["status"] == "ready"
        assert result["canonical_ref"] == f"/api/v1/files/{visible_record.file_id}"
        assert result["provenance"] == {
            "indexed_from": "canonical-control-plane",
            "collection": "files",
        }

        assert (
            _items(await _search(http, q="application/pdf"))[0]["resource_id"]
            == visible_record.file_id
        )
        assert (
            _items(await _search(http, q=artifact_id))[0]["resource_id"] == visible_record.file_id
        )
        assert (await _search(http, q="needle-that-must-not-be-searchable"))["total"] == 0
        assert (
            _items(await _search(http, project_id=visible.id))[0]["resource_id"]
            == visible_record.file_id
        )

        all_visible = await _search(http)
        assert all_visible["total"] == 1
        serialized = json.dumps(all_visible, sort_keys=True)
        assert hidden_record.file_id not in serialized
        assert hidden.id not in serialized
        assert "hidden-owner" not in serialized

        hidden_exact = await _search(http, id=hidden_record.file_id)
        assert hidden_exact["total"] == 0
        assert hidden_record.file_id not in json.dumps(hidden_exact, sort_keys=True)

        assert any(
            call.action == "file:list" and call.context.project_id == hidden.id
            for call in authorization.calls
        )

        rebuilt = await control_plane.rebuild_search_index()
        assert rebuilt >= 7

        await providers.files.delete_file(
            visible_record.file_id,
            _data_context(project_id=visible.id, owner_id="visible-owner"),
        )
        deleted = await _search(http, id=visible_record.file_id)
        assert deleted["total"] == 0
        assert visible_record.file_id not in json.dumps(deleted, sort_keys=True)

    asyncio.run(scenario())
