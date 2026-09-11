"""Task/Run northbound operations behind the stable Control Plane façade."""

from __future__ import annotations

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.kernel import PlatformKernel
from ai_multi_agent_platform.kernel.repository import EventRepository

from .authorization_service import ControlPlaneAuthorization
from .models import PageQuery, RequestContext, paginate
from .request_validation import optional_string, require_key, required_string, resolve_owner
from .resources import run_resource, task_resource
from .scope_store import ScopeStore


class ControlPlaneTaskRunService:
    """Own Task/Run API mechanics while canonical lifecycle remains in PlatformKernel."""

    def __init__(
        self,
        *,
        kernel: PlatformKernel,
        events: EventRepository,
        scopes: ScopeStore,
        authorization: ControlPlaneAuthorization,
    ) -> None:
        self._kernel = kernel
        self._events = events
        self._scopes = scopes
        self._authorization = authorization

    async def create_task(
        self,
        context: RequestContext,
        payload: dict[str, JsonValue],
        *,
        authorization_payload: dict[str, JsonValue] | None = None,
    ) -> dict[str, JsonValue]:
        owner_type, owner_id = resolve_owner(context.actor, payload)
        project_id = optional_string(payload, "project_id")
        if project_id is not None:
            self._scopes.get_project(project_id)
        bound_payload = payload if authorization_payload is None else authorization_payload
        await self._authorization.authorize(
            context,
            "task:create",
            "tasks",
            owner_type=owner_type,
            owner_id=owner_id,
            project_id=project_id,
            request_payload_digest=ControlPlaneAuthorization.payload_digest(bound_payload),
        )
        state = await self._kernel.create_task(
            idempotency_key=require_key(context),
            title=required_string(payload, "title"),
            objective=required_string(payload, "objective"),
            owner_type=owner_type,
            owner_id=owner_id,
            project_id=project_id,
            task_id=optional_string(payload, "task_id"),
            actor_ref=context.actor.principal_ref,
            source="control-plane",
        )
        return task_resource(state)

    async def list_tasks(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> dict[str, JsonValue]:
        await self._authorization.authorize(context, "task:list", "tasks")
        resources: list[dict[str, JsonValue]] = []
        for task_id in await self.task_ids():
            task = await self._kernel.get_task(task_id)
            if await self._authorization.allowed_for_task(context, "task:list", task_id, task):
                resources.append(task_resource(task))
        return paginate(resources, query)

    async def get_task(
        self,
        context: RequestContext,
        task_id: str,
    ) -> dict[str, JsonValue]:
        task = await self._kernel.get_task(task_id)
        await self._authorization.authorize_for_task(context, "task:read", task_id, task)
        return task_resource(task)

    async def queue_task(
        self,
        context: RequestContext,
        task_id: str,
    ) -> dict[str, JsonValue]:
        task = await self._kernel.get_task(task_id)
        await self._authorization.authorize_for_task(context, "task:queue", task_id, task)
        state = await self._kernel.ready_task(
            idempotency_key=require_key(context),
            task_id=task_id,
            actor_ref=context.actor.principal_ref,
            source="control-plane",
        )
        return task_resource(state)

    async def start_task(
        self,
        context: RequestContext,
        task_id: str,
    ) -> dict[str, JsonValue]:
        task = await self._kernel.get_task(task_id)
        await self._authorization.authorize_for_task(context, "task:start", task_id, task)
        state = await self._kernel.start_task(
            idempotency_key=require_key(context),
            task_id=task_id,
            actor_ref=context.actor.principal_ref,
            source="control-plane",
        )
        return run_resource(state)

    async def cancel_task(
        self,
        context: RequestContext,
        task_id: str,
    ) -> dict[str, JsonValue]:
        task = await self._kernel.get_task(task_id)
        await self._authorization.authorize_for_task(context, "task:cancel", task_id, task)
        state = await self._kernel.cancel_task(
            idempotency_key=require_key(context),
            task_id=task_id,
            actor_ref=context.actor.principal_ref,
            source="control-plane",
        )
        return task_resource(state)

    async def retry_task(
        self,
        context: RequestContext,
        task_id: str,
    ) -> dict[str, JsonValue]:
        task = await self._kernel.get_task(task_id)
        await self._authorization.authorize_for_task(context, "task:retry", task_id, task)
        state = await self._kernel.retry_task(
            idempotency_key=require_key(context),
            task_id=task_id,
            actor_ref=context.actor.principal_ref,
            source="control-plane",
        )
        return run_resource(state)

    async def list_runs(
        self,
        context: RequestContext,
        query: PageQuery,
        *,
        task_id: str | None = None,
    ) -> dict[str, JsonValue]:
        await self._authorization.authorize(context, "run:list", task_id or "runs")
        task_ids = (task_id,) if task_id is not None else await self.task_ids()
        resources: list[dict[str, JsonValue]] = []
        for current_task_id in task_ids:
            task = await self._kernel.get_task(current_task_id)
            for run_id in task.run_ids:
                if await self._authorization.allowed_for_task(context, "run:list", run_id, task):
                    resources.append(
                        run_resource(await self._kernel.get_run(current_task_id, run_id))
                    )
        return paginate(resources, query)

    async def get_run(
        self,
        context: RequestContext,
        run_id: str,
        *,
        task_id: str | None = None,
    ) -> dict[str, JsonValue]:
        if task_id is not None:
            task = await self._kernel.get_task(task_id)
            run = await self._kernel.get_run(task_id, run_id)
            await self._authorization.authorize_for_task(context, "run:read", run_id, task)
            return run_resource(run)
        for current_task_id in await self.task_ids():
            task = await self._kernel.get_task(current_task_id)
            if run_id in task.run_ids:
                run = await self._kernel.get_run(current_task_id, run_id)
                await self._authorization.authorize_for_task(context, "run:read", run_id, task)
                return run_resource(run)
        raise ContractError(ErrorCode.NOT_FOUND, f"run not found: {run_id}")

    async def cancel_run(
        self,
        context: RequestContext,
        task_id: str,
        run_id: str,
    ) -> dict[str, JsonValue]:
        task = await self._kernel.get_task(task_id)
        await self._kernel.get_run(task_id, run_id)
        await self._authorization.authorize_for_task(context, "run:cancel", run_id, task)
        state = await self._kernel.cancel_run(
            idempotency_key=require_key(context),
            task_id=task_id,
            run_id=run_id,
            actor_ref=context.actor.principal_ref,
            source="control-plane",
        )
        return run_resource(state)

    async def task_ids(self) -> tuple[str, ...]:
        return tuple(
            stream_id
            for stream_id in await self._events.list_stream_ids()
            if stream_id.startswith("task_")
        )
