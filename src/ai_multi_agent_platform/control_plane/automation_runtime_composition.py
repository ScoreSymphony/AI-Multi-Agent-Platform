"""Runtime-complete Control Plane composition for canonical Automation (#18)."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from ai_multi_agent_platform.automation import AutomationEventSink, SqliteAutomationRepository
from ai_multi_agent_platform.automation.runtime import (
    AutomationCommandRecord,
    AutomationRuntime,
    AutomationRuntimeState,
    AutomationRuntimeTick,
    InMemoryAutomationRuntimeState,
    SqliteAutomationRuntimeState,
)
from ai_multi_agent_platform.automation.workspace_event_scope import (
    CanonicalWorkspaceEventScopeResolver,
)
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import validate_id
from ai_multi_agent_platform.kernel.repository import EventRepository
from ai_multi_agent_platform.security.authorization import infer_actor_identity
from ai_multi_agent_platform.workspaces import RunWorkspaceBindingRepository, WorkspaceProvider

from .models import RequestContext
from .plugin_terminal_composition import ControlPlane as _BaseControlPlane
from .plugin_terminal_composition import ControlPlaneASGI as _BaseControlPlaneASGI
from .plugin_terminal_composition import ControlPlaneHTTP as ControlPlaneHTTP
from .plugin_terminal_composition import build_openapi as build_openapi
from .service import _payload_digest

AUTOMATION_STATE_ENV = "AI_MULTI_AGENT_PLATFORM_AUTOMATION_STATE"
_REPLAYED_AUTOMATION_COMMANDS = frozenset(
    {
        "automation.create",
        "automation.update",
        "automation.pause",
        "automation.resume",
        "automation.disable",
        "automation.invalidate",
        "automation.revalidate",
    }
)
_INTERNAL_AUTOMATION_COMMANDS = frozenset({"automation.event", "automation.evaluate"})


class ControlPlane(_BaseControlPlane):
    """Current Control Plane plus an autonomous, restart-safe reference Automation runtime.

    ``automation_state_path`` activates the durable reference path. When omitted, the low-level
    embedding remains explicitly ephemeral for tests and transient local use. Deployments may set
    ``AI_MULTI_AGENT_PLATFORM_AUTOMATION_STATE`` instead of passing the path directly.
    """

    def __init__(
        self,
        *args: Any,
        automation_state_path: str | Path | None = None,
        automation_runtime_state: AutomationRuntimeState | None = None,
        automation_runtime_poll_seconds: float = 1.0,
        **kwargs: Any,
    ) -> None:
        configured_path = automation_state_path
        if configured_path is None:
            configured_path = os.environ.get(AUTOMATION_STATE_ENV)
        state_path = None if configured_path is None else Path(configured_path)

        events = cast(EventRepository | None, kwargs.get("events"))
        if events is None:
            raise ValueError(
                "Automation runtime composition requires the canonical EventRepository"
            )
        workspace_provider = cast(WorkspaceProvider | None, kwargs.get("workspace_provider"))
        run_workspace_bindings = cast(
            RunWorkspaceBindingRepository | None,
            kwargs.get("run_workspace_bindings"),
        )

        custom_service = kwargs.get("automation_service") is not None
        if (
            state_path is not None
            and not custom_service
            and kwargs.get("automation_repository") is None
        ):
            kwargs["automation_repository"] = SqliteAutomationRepository(state_path)

        if automation_runtime_state is None:
            automation_runtime_state = (
                SqliteAutomationRuntimeState(state_path)
                if state_path is not None
                else InMemoryAutomationRuntimeState()
            )
        self._automation_runtime_state = automation_runtime_state

        provided_sink = cast(AutomationEventSink | None, kwargs.get("automation_event_sink"))
        if not custom_service:

            async def runtime_audit_sink(event: dict[str, JsonValue]) -> None:
                await automation_runtime_state.append_audit_event(event)
                if provided_sink is not None:
                    await provided_sink(event)

            kwargs["automation_event_sink"] = runtime_audit_sink

        super().__init__(*args, **kwargs)
        if not custom_service and (
            workspace_provider is not None or run_workspace_bindings is not None
        ):
            self.automation_service.configure_workspace_event_scope_resolver(
                CanonicalWorkspaceEventScopeResolver(
                    workspace_provider=workspace_provider,
                    run_workspace_bindings=run_workspace_bindings,
                )
            )
        self._automation_command_locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._automation_runtime = AutomationRuntime(
            service=self.automation_service,
            scheduler=self.automation_scheduler,
            events=events,
            state=automation_runtime_state,
            poll_interval_seconds=automation_runtime_poll_seconds,
        )

    @property
    def automation_runtime(self) -> AutomationRuntime:
        return self._automation_runtime

    @property
    def automation_runtime_state(self) -> AutomationRuntimeState:
        return self._automation_runtime_state

    async def start_automation_runtime(self) -> None:
        await self._automation_runtime.start()

    async def stop_automation_runtime(self) -> None:
        await self._automation_runtime.stop()

    async def run_automation_runtime_once(self) -> AutomationRuntimeTick:
        return await self._automation_runtime.run_once()

    async def execute_command(
        self,
        context: RequestContext,
        command: str,
        resource_ref: str,
        payload: dict[str, JsonValue] | None = None,
    ) -> dict[str, JsonValue]:
        effective_payload = payload or {}
        if command in _INTERNAL_AUTOMATION_COMMANDS:
            self._require_internal_automation_authority(context, command, effective_payload)

        if command not in _REPLAYED_AUTOMATION_COMMANDS or context.idempotency_key is None:
            return await super().execute_command(context, command, resource_ref, effective_payload)

        # Idempotency replay is not an authorization cache. Re-check the current policy and the
        # requested project scope before returning a previously persisted result.
        await self._authorize_replayed_automation_command(
            context,
            command,
            resource_ref,
            effective_payload,
        )

        lock_key = (context.actor.principal_ref, context.idempotency_key)
        lock = self._automation_command_locks.setdefault(lock_key, asyncio.Lock())
        async with lock:
            digest = _payload_digest(effective_payload)
            existing = await self._automation_runtime_state.get_command(*lock_key)
            if existing is not None:
                self._require_same_command(
                    existing,
                    command=command,
                    resource_ref=resource_ref,
                    payload_digest=digest,
                )
                return dict(existing.result)

            result = await super().execute_command(
                context,
                command,
                resource_ref,
                effective_payload,
            )
            persisted = await self._automation_runtime_state.save_command(
                AutomationCommandRecord(
                    principal_ref=context.actor.principal_ref,
                    idempotency_key=context.idempotency_key,
                    command=command,
                    resource_ref=resource_ref,
                    payload_digest=digest,
                    result=dict(result),
                )
            )
            self._require_same_command(
                persisted,
                command=command,
                resource_ref=resource_ref,
                payload_digest=digest,
            )
            return dict(persisted.result)

    async def _authorize_replayed_automation_command(
        self,
        context: RequestContext,
        command: str,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> None:
        digest = _payload_digest(payload)
        if command == "automation.create":
            project_ids = _requested_project_ids(payload)
            if not project_ids:
                await self._authorize(
                    context,
                    command,
                    resource_ref,
                    owner_type=context.actor.owner_type,
                    owner_id=context.actor.owner_id,
                    request_payload_digest=digest,
                )
                return
            for project_id in project_ids:
                await self._authorize(
                    context,
                    command,
                    resource_ref,
                    owner_type=context.actor.owner_type,
                    owner_id=context.actor.owner_id,
                    project_id=project_id,
                    request_payload_digest=digest,
                )
            return

        automation = await self.automation_service.get_automation(resource_ref)
        await self._authorize_automation(context, command, automation)

        if command != "automation.update":
            return
        for project_id in _requested_task_template_project_ids(payload):
            if project_id == automation.project_id:
                continue
            await self._authorize(
                context,
                command,
                resource_ref,
                owner_type=automation.identity.owner_type,
                owner_id=automation.identity.owner_id,
                project_id=project_id,
                request_payload_digest=digest,
            )

    def _require_internal_automation_authority(
        self,
        context: RequestContext,
        command: str,
        payload: Mapping[str, JsonValue],
    ) -> None:
        actor_type = context.actor.actor_type
        if actor_type is None:
            actor_type = infer_actor_identity(context.actor.principal_ref).actor_type.value
        if actor_type != "service":
            raise ContractError(
                ErrorCode.FORBIDDEN,
                f"{command} is reserved for trusted internal service authority",
            )
        if command == "automation.evaluate" and payload.get("now") is not None:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "automation.evaluate cannot override runtime time",
            )
        if command == "automation.event":
            event_id = payload.get("event_id")
            if not isinstance(event_id, str):
                raise ContractError(
                    ErrorCode.INVALID_REQUEST,
                    "automation.event requires a canonical event_id",
                )
            validate_id(event_id, "event")
            if payload.get("fired_at") is not None:
                raise ContractError(
                    ErrorCode.INVALID_REQUEST,
                    "automation.event cannot override canonical event time",
                )

    @staticmethod
    def _require_same_command(
        record: AutomationCommandRecord,
        *,
        command: str,
        resource_ref: str,
        payload_digest: str,
    ) -> None:
        if (
            record.command == command
            and record.resource_ref == resource_ref
            and record.payload_digest == payload_digest
        ):
            return
        raise ContractError(
            ErrorCode.CONFLICT,
            "Automation Idempotency-Key was reused for a different command or payload",
            details={
                "existing_command": record.command,
                "existing_resource_ref": record.resource_ref,
            },
        )


class ControlPlaneASGI:
    """Current ASGI composition with Automation runtime bound to ASGI lifespan."""

    def __init__(self, http: Any) -> None:
        self._inner = _BaseControlPlaneASGI(http)
        control_plane = getattr(http, "_control_plane", None)
        self._control_plane = control_plane if isinstance(control_plane, ControlPlane) else None

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "lifespan" or self._control_plane is None:
            await self._inner(scope, receive, send)
            return

        while True:
            message = await receive()
            message_type = message.get("type")
            if message_type == "lifespan.startup":
                try:
                    await self._control_plane.start_automation_runtime()
                except Exception as exc:
                    await send({"type": "lifespan.startup.failed", "message": str(exc)})
                    return
                await send({"type": "lifespan.startup.complete"})
                continue
            if message_type == "lifespan.shutdown":
                try:
                    await self._control_plane.stop_automation_runtime()
                except Exception as exc:
                    await send({"type": "lifespan.shutdown.failed", "message": str(exc)})
                    return
                await send({"type": "lifespan.shutdown.complete"})
                return


def _requested_project_ids(payload: Mapping[str, JsonValue]) -> tuple[str, ...]:
    projects: list[str] = []
    project_id = payload.get("project_id")
    if isinstance(project_id, str):
        projects.append(project_id)
    projects.extend(_requested_task_template_project_ids(payload))
    return tuple(dict.fromkeys(projects))


def _requested_task_template_project_ids(
    payload: Mapping[str, JsonValue],
) -> tuple[str, ...]:
    template = payload.get("task_template")
    if not isinstance(template, dict):
        return ()
    project_id = template.get("project_id")
    return (project_id,) if isinstance(project_id, str) else ()
