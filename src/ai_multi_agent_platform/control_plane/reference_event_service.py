"""Reference and event northbound operations behind the stable Control Plane façade."""

from __future__ import annotations

from collections.abc import AsyncIterator

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.interfaces import EventProvider
from ai_multi_agent_platform.contracts.types import JsonValue, OperationControl
from ai_multi_agent_platform.kernel import PlatformKernel
from ai_multi_agent_platform.kernel.repository import EventRepository

from .authorization_service import ControlPlaneAuthorization
from .models import PageQuery, RequestContext, paginate
from .resources import ReferenceCollection, deduplicate, event_resource, references_for_task
from .task_run_service import ControlPlaneTaskRunService


class ControlPlaneReferenceEventService:
    """Own reference projections and Task event access without owning canonical truth."""

    def __init__(
        self,
        *,
        kernel: PlatformKernel,
        events: EventRepository,
        authorization: ControlPlaneAuthorization,
        task_runs: ControlPlaneTaskRunService,
        live_events: EventProvider | None = None,
    ) -> None:
        self._kernel = kernel
        self._events = events
        self._authorization = authorization
        self._task_runs = task_runs
        self._live_events = live_events

    async def list_references(
        self,
        context: RequestContext,
        collection: ReferenceCollection,
        query: PageQuery,
    ) -> dict[str, JsonValue]:
        await self._authorization.authorize(context, f"{collection}:list", collection)
        resources: list[dict[str, JsonValue]] = []
        for task_id in await self._task_runs.task_ids():
            task = await self._kernel.get_task(task_id)
            for resource in references_for_task(task, collection):
                resource_id = resource["id"]
                if isinstance(resource_id, str) and await self._authorization.allowed_for_task(
                    context,
                    f"{collection}:list",
                    resource_id,
                    task,
                ):
                    resources.append(resource)
        return paginate(deduplicate(resources), query)

    async def get_reference(
        self,
        context: RequestContext,
        collection: ReferenceCollection,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        for task_id in await self._task_runs.task_ids():
            task = await self._kernel.get_task(task_id)
            for resource in references_for_task(task, collection):
                if resource.get("id") != resource_id:
                    continue
                await self._authorization.authorize_for_task(
                    context,
                    f"{collection[:-1]}:read",
                    resource_id,
                    task,
                )
                return resource
        raise ContractError(ErrorCode.NOT_FOUND, f"{collection[:-1]} not found: {resource_id}")

    async def timeline(
        self,
        context: RequestContext,
        task_id: str,
        query: PageQuery,
    ) -> dict[str, JsonValue]:
        task = await self._kernel.get_task(task_id)
        await self._authorization.authorize_for_task(context, "event:list", task_id, task)
        events = [event_resource(event) for event in await self._events.read_events(task_id)]
        return paginate(events, query)

    async def subscribe_task_events(
        self,
        context: RequestContext,
        task_id: str,
        *,
        after_event_id: str | None = None,
    ) -> AsyncIterator[dict[str, JsonValue]]:
        task = await self._kernel.get_task(task_id)
        await self._authorization.authorize_for_task(context, "event:subscribe", task_id, task)

        live_events = self._live_events
        if live_events is not None:

            async def live_iterator() -> AsyncIterator[dict[str, JsonValue]]:
                async for event in live_events.subscribe(
                    task_id,
                    after_event_id=after_event_id,
                    control=OperationControl(),
                ):
                    yield event_resource(event)

            return live_iterator()

        events = await self._events.read_events(task_id)
        start_index = 0
        if after_event_id is not None:
            for index, event in enumerate(events):
                if event.id == after_event_id:
                    start_index = index + 1
                    break
            else:
                raise ContractError(
                    ErrorCode.NOT_FOUND,
                    f"event cursor not found: {after_event_id}",
                )

        async def repository_iterator() -> AsyncIterator[dict[str, JsonValue]]:
            for event in events[start_index:]:
                yield event_resource(event)

        return repository_iterator()
