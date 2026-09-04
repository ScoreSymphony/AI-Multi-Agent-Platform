from __future__ import annotations

import asyncio

from ai_multi_agent_platform.contracts.types import OperationContext
from ai_multi_agent_platform.control_plane import ControlPlane, ControlPlaneHTTP, HTTPRequest
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.search import (
    SEARCH_INDEX_SCHEMA_VERSION,
    LocalSearchProvider,
    SearchDocument,
)
from ai_multi_agent_platform.testing import (
    FakeAuthorizationProvider,
    FakeLifecycleBackend,
    FakeOrchestrator,
)


class CountingLocalSearchProvider(LocalSearchProvider):
    def __init__(self, **kwargs: str) -> None:
        super().__init__(**kwargs)
        self.rebuild_calls = 0

    async def rebuild(
        self,
        documents: tuple[SearchDocument, ...],
        context: OperationContext,
    ) -> None:
        self.rebuild_calls += 1
        await super().rebuild(documents, context)


class CheckpointlessSearchProvider(CountingLocalSearchProvider):
    async def index_checkpoint(self, context: OperationContext):  # type: ignore[no-untyped-def]
        del context
        return None

    async def mark_stale(self, reason: str, context: OperationContext) -> None:
        del reason, context


def _context() -> OperationContext:
    return OperationContext(correlation_id="issue-45-checkpoint")


def _stack(
    provider: LocalSearchProvider,
    *,
    rebuild_before_query: bool,
) -> tuple[ControlPlane, ControlPlaneHTTP, PlatformKernel]:
    repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
    )
    control_plane = ControlPlane(
        kernel=kernel,
        events=repository,
        authorization=FakeAuthorizationProvider(),
        search_provider=provider,
        search_rebuild_before_query=rebuild_before_query,
    )
    return control_plane, ControlPlaneHTTP(control_plane), kernel


async def _search(http: ControlPlaneHTTP, **query: str) -> dict[str, object]:
    response = await http.handle(
        HTTPRequest(method="GET", path="/api/v1/search", query=query)
    )
    assert response.status == 200, response.body
    assert isinstance(response.body, dict)
    return response.body


def test_local_provider_checkpoint_tracks_generation_freshness_and_document_count() -> None:
    async def scenario() -> None:
        provider = LocalSearchProvider()
        context = _context()
        first_id = new_id("task")
        second_id = new_id("task")
        first = SearchDocument(resource_type="task", resource_id=first_id, title="First")
        second = SearchDocument(resource_type="task", resource_id=second_id, title="Second")

        assert await provider.index_checkpoint(context) is None

        await provider.upsert(first, context)
        checkpoint = await provider.index_checkpoint(context)
        assert checkpoint is not None
        assert checkpoint.generation == 1
        assert checkpoint.schema_version == SEARCH_INDEX_SCHEMA_VERSION
        assert checkpoint.document_count == 1
        assert checkpoint.rebuilt_at is None
        assert checkpoint.stale is True
        assert checkpoint.stale_reason == "incremental update before full rebuild"

        await provider.mark_stale("missed canonical event", context)
        stale = await provider.index_checkpoint(context)
        assert stale is not None
        assert stale.generation == 1
        assert stale.stale is True
        assert stale.stale_reason == "missed canonical event"

        await provider.upsert(second, context)
        still_stale = await provider.index_checkpoint(context)
        assert still_stale is not None
        assert still_stale.generation == 2
        assert still_stale.document_count == 2
        assert still_stale.stale is True
        assert still_stale.stale_reason == "missed canonical event"

        await provider.rebuild((first, second), context)
        rebuilt = await provider.index_checkpoint(context)
        assert rebuilt is not None
        assert rebuilt.generation == 3
        assert rebuilt.document_count == 2
        assert rebuilt.rebuilt_at is not None
        assert rebuilt.stale is False
        assert rebuilt.stale_reason is None
        assert rebuilt.to_json()["schema_version"] == SEARCH_INDEX_SCHEMA_VERSION

        await provider.delete(second.resource_type, second.resource_id, context)
        after_delete = await provider.index_checkpoint(context)
        assert after_delete is not None
        assert after_delete.generation == 4
        assert after_delete.document_count == 1
        assert after_delete.stale is False

    asyncio.run(scenario())


def test_checkpointed_control_plane_reuses_fresh_index_and_recovers_when_marked_stale() -> None:
    async def scenario() -> None:
        provider = CountingLocalSearchProvider()
        control_plane, http, kernel = _stack(provider, rebuild_before_query=False)
        project_id = new_id("project")
        first = await kernel.create_task(
            idempotency_key="checkpoint-first",
            title="Checkpoint first task",
            objective="Prime the derived index",
            owner_type="user",
            owner_id="alice",
            project_id=project_id,
        )

        first_page = await _search(http, type="task", id=first.task_id)
        assert first_page["total"] == 1
        assert provider.rebuild_calls == 1

        repeated = await _search(http, type="task", id=first.task_id)
        assert repeated["total"] == 1
        assert provider.rebuild_calls == 1

        second = await kernel.create_task(
            idempotency_key="checkpoint-second",
            title="Checkpoint second task",
            objective="Require stale-index recovery",
            owner_type="user",
            owner_id="alice",
            project_id=project_id,
        )
        await control_plane.mark_search_index_stale("missed event delivery")
        stale = await control_plane.search_index_checkpoint()
        assert stale is not None
        assert stale.stale is True
        assert stale.stale_reason == "missed event delivery"

        recovered = await _search(http, type="task", id=second.task_id)
        assert recovered["total"] == 1
        assert provider.rebuild_calls == 2
        checkpoint = await control_plane.search_index_checkpoint()
        assert checkpoint is not None
        assert checkpoint.stale is False
        assert checkpoint.document_count >= 4

    asyncio.run(scenario())


def test_checkpointed_mode_falls_back_to_rebuild_for_legacy_provider_without_checkpoints() -> None:
    async def scenario() -> None:
        provider = CheckpointlessSearchProvider()
        _, http, kernel = _stack(provider, rebuild_before_query=False)
        task = await kernel.create_task(
            idempotency_key="checkpoint-legacy",
            title="Legacy provider task",
            objective="Preserve correctness without checkpoint capability",
            owner_type="user",
            owner_id="alice",
            project_id=new_id("project"),
        )

        assert (await _search(http, type="task", id=task.task_id))["total"] == 1
        assert (await _search(http, type="task", id=task.task_id))["total"] == 1
        assert provider.rebuild_calls == 2

    asyncio.run(scenario())


def test_checkpointed_mode_fails_closed_for_incompatible_index_schema() -> None:
    async def scenario() -> None:
        provider = CountingLocalSearchProvider(index_schema_version="legacy-v0")
        _, http, kernel = _stack(provider, rebuild_before_query=False)
        task = await kernel.create_task(
            idempotency_key="checkpoint-schema",
            title="Schema mismatch task",
            objective="Do not serve an incompatible derived index",
            owner_type="user",
            owner_id="alice",
            project_id=new_id("project"),
        )

        response = await http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/search",
                query={"type": "task", "id": task.task_id},
            )
        )
        assert response.status == 400
        assert isinstance(response.body, dict)
        assert response.body["code"] == "unsupported_capability"
        assert provider.rebuild_calls == 1

    asyncio.run(scenario())
