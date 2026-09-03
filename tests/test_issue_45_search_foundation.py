from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import OperationContext
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.search import (
    LocalSearchProvider,
    SearchDocument,
    SearchMode,
    SearchQuery,
    document_from_resource,
)


def _context() -> OperationContext:
    return OperationContext(correlation_id="corr_issue_45")


def test_exact_lookup_uses_canonical_identity_and_result_shape() -> None:
    async def scenario() -> None:
        provider = LocalSearchProvider()
        project_id = new_id("project")
        task_id = new_id("task")
        await provider.upsert(
            SearchDocument(
                resource_type="task",
                resource_id=task_id,
                title="Build global search",
                summary="Create the canonical search foundation.",
                project_id=project_id,
                status="pending",
                canonical_ref=f"/api/v1/tasks/{task_id}",
            ),
            _context(),
        )

        page = await provider.search(SearchQuery(exact_id=task_id), _context())

        assert page.total == 1
        assert page.items[0].resource_type == "task"
        assert page.items[0].resource_id == task_id
        assert page.items[0].canonical_ref == f"/api/v1/tasks/{task_id}"
        assert page.items[0].matched_fields == ("resource_id",)
        assert page.items[0].to_json()["resource_id"] == task_id

    asyncio.run(scenario())


def test_keyword_and_metadata_filters_span_multiple_resource_types() -> None:
    async def scenario() -> None:
        provider = LocalSearchProvider()
        project_a = new_id("project")
        project_b = new_id("project")
        task_a = new_id("task")
        task_b = new_id("task")
        await provider.rebuild(
            (
                SearchDocument(
                    resource_type="project",
                    resource_id=project_a,
                    title="Search project",
                    project_id=project_a,
                ),
                SearchDocument(
                    resource_type="task",
                    resource_id=task_a,
                    title="Search indexing",
                    summary="Index Project and Task metadata",
                    project_id=project_a,
                    status="running",
                    tags=("search", "core"),
                ),
                SearchDocument(
                    resource_type="task",
                    resource_id=task_b,
                    title="Unrelated task",
                    project_id=project_b,
                    status="running",
                ),
            ),
            _context(),
        )

        page = await provider.search(
            SearchQuery(
                text="search",
                resource_types=("task",),
                project_id=project_a,
                statuses=("running",),
                tags=("search",),
            ),
            _context(),
        )

        assert page.total == 1
        assert page.items[0].resource_id == task_a
        assert "title" in page.items[0].matched_fields

    asyncio.run(scenario())


def test_updated_at_range_filter_is_timezone_aware_and_inclusive() -> None:
    async def scenario() -> None:
        provider = LocalSearchProvider()
        context = _context()
        old_task = new_id("task")
        current_task = new_id("task")
        future_task = new_id("task")
        await provider.rebuild(
            (
                SearchDocument(
                    resource_type="task",
                    resource_id=old_task,
                    title="Old task",
                    updated_at="2026-09-01T09:00:00+00:00",
                ),
                SearchDocument(
                    resource_type="task",
                    resource_id=current_task,
                    title="Current task",
                    updated_at="2026-09-03T10:00:00+00:00",
                ),
                SearchDocument(
                    resource_type="task",
                    resource_id=future_task,
                    title="Future task",
                    updated_at="2026-09-05T12:00:00+00:00",
                ),
            ),
            context,
        )

        page = await provider.search(
            SearchQuery(
                mode=SearchMode.METADATA,
                updated_after=datetime(2026, 9, 3, 10, tzinfo=UTC),
                updated_before=datetime(2026, 9, 4, tzinfo=UTC),
            ),
            context,
        )

        assert page.total == 1
        assert page.items[0].resource_id == current_task

        with pytest.raises(ValueError, match="timezone-aware"):
            SearchQuery(
                mode=SearchMode.METADATA,
                updated_after=datetime(2026, 9, 3, 10),
            )

    asyncio.run(scenario())


def test_upsert_delete_and_full_rebuild_do_not_leave_stale_documents() -> None:
    async def scenario() -> None:
        provider = LocalSearchProvider()
        task_id = new_id("task")
        context = _context()
        await provider.upsert(
            SearchDocument(resource_type="task", resource_id=task_id, title="Old title"),
            context,
        )
        await provider.upsert(
            SearchDocument(resource_type="task", resource_id=task_id, title="New title"),
            context,
        )

        old_page = await provider.search(SearchQuery(text="Old title"), context)
        new_page = await provider.search(SearchQuery(text="New title"), context)
        assert old_page.total == 0
        assert new_page.total == 1

        await provider.delete("task", task_id, context)
        assert (await provider.search(SearchQuery(exact_id=task_id), context)).total == 0

        project_id = new_id("project")
        await provider.rebuild(
            (
                SearchDocument(
                    resource_type="project",
                    resource_id=project_id,
                    title="Only canonical survivor",
                    project_id=project_id,
                ),
            ),
            context,
        )
        all_items = await provider.search(SearchQuery(mode=SearchMode.METADATA), context)
        assert all_items.total == 1
        assert all_items.items[0].resource_id == project_id

    asyncio.run(scenario())


def test_search_pagination_uses_opaque_cursor() -> None:
    async def scenario() -> None:
        provider = LocalSearchProvider()
        context = _context()
        documents = tuple(
            SearchDocument(resource_type="task", resource_id=new_id("task"), title=f"Task {index}")
            for index in range(5)
        )
        await provider.rebuild(documents, context)

        first = await provider.search(
            SearchQuery(mode=SearchMode.METADATA, limit=2, sort="id", direction="asc"),
            context,
        )
        second = await provider.search(
            SearchQuery(
                mode=SearchMode.METADATA,
                limit=2,
                sort="id",
                direction="asc",
                cursor=first.next_cursor,
            ),
            context,
        )

        assert first.total == 5
        assert len(first.items) == 2
        assert first.next_cursor is not None
        assert len(second.items) == 2
        assert {item.resource_id for item in first.items}.isdisjoint(
            {item.resource_id for item in second.items}
        )

    asyncio.run(scenario())


def test_semantic_mode_is_optional_and_explicitly_degraded() -> None:
    async def scenario() -> None:
        provider = LocalSearchProvider()
        with pytest.raises(ContractError) as exc_info:
            await provider.search(
                SearchQuery(text="meaning", mode=SearchMode.SEMANTIC),
                _context(),
            )
        assert exc_info.value.code is ErrorCode.UNSUPPORTED_CAPABILITY

    asyncio.run(scenario())


def test_canonical_control_plane_resource_maps_to_search_document() -> None:
    project_id = new_id("project")
    task_id = new_id("task")
    document = document_from_resource(
        {
            "id": task_id,
            "type": "task",
            "title": "Platform-wide search",
            "objective": "Search canonical resources without backend coupling.",
            "status": "running",
            "project_id": project_id,
            "revision": 7,
            "updated_at": "2026-09-03T03:00:00+00:00",
        }
    )

    assert document.resource_id == task_id
    assert document.resource_type == "task"
    assert document.project_id == project_id
    assert document.status == "running"
    assert document.version == "7"
    assert document.canonical_ref == f"/api/v1/tasks/{task_id}"
    assert document.provenance == {"indexed_from": "canonical-control-plane"}
