"""Authorization hardening for the fully composed Control Plane.

Later Control Plane domains must preserve issue-#15 exact-action approval binding rather
than accidentally falling back to action/resource-only authorization when they override
foundation commands.
"""

from __future__ import annotations

from typing import Any, cast

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue

from .extensions import _reject_private_payload, _validate_command_name
from .models import RequestContext
from .run_workspace_contract import _has_binding_fields
from .service import _optional_string, _payload_digest, _require_key, _required_string
from .task_management_contract import TASK_MANAGEMENT_COMMANDS, _require_idempotency_key
from .workspace_contract import (
    _data_access_context,
    _workspace_access_mode,
    _workspace_files,
    _workspace_resource,
    _workspace_retention,
    _workspace_source_refs,
)


class AuthorizationBoundaryHardeningMixin:
    """Preserve exact-payload authorization across later composed domain overrides."""

    @property
    def authorization_configured(self) -> bool:
        return cast(Any, self)._authorization is not None

    async def _authorize_for_task(
        self,
        context: RequestContext,
        action: str,
        resource_ref: str,
        task: Any,
        *,
        request_payload_digest: str | None = None,
    ) -> None:
        cp = cast(Any, self)
        await cp._authorize(
            context,
            action,
            resource_ref,
            owner_type=task.task.owner_ref.type,
            owner_id=task.task.owner_ref.id,
            project_id=task.task.project_id,
            request_payload_digest=request_payload_digest,
        )

    async def execute_command(
        self,
        context: RequestContext,
        command: str,
        resource_ref: str,
        payload: dict[str, JsonValue] | None = None,
    ) -> dict[str, JsonValue]:
        """Authorize generic commands against the exact payload before dispatch."""

        cp = cast(Any, self)
        _validate_command_name(command)
        handler = cp._command_handlers.get(command)
        if handler is None:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"canonical command is not registered: {command}",
                details={"command": command},
            )
        if context.idempotency_key is None:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "Idempotency-Key is required for mutating commands",
                details={"header": "Idempotency-Key"},
            )

        effective_payload = payload or {}
        # Task-management handlers perform a richer project/task-scoped authorization
        # below. Avoid a second generic approval for the same command.
        if command not in TASK_MANAGEMENT_COMMANDS:
            await cp._authorize(
                context,
                command,
                resource_ref,
                request_payload_digest=_payload_digest(effective_payload),
            )
        result = await handler(context, resource_ref, effective_payload)
        _reject_private_payload(result)
        return cast(dict[str, JsonValue], result)

    async def _update_management_command(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        cp = cast(Any, self)
        task = await cp._kernel.get_task(resource_ref)
        await cp._authorize_for_task(
            context,
            "task:update-management",
            resource_ref,
            task,
            request_payload_digest=_payload_digest(payload),
        )
        key = _require_idempotency_key(context)
        prepared = await cp._task_management.prepare(resource_ref, payload)
        await cp._task_management.commit(
            prepared,
            idempotency_key=f"{key}:task-management",
            actor_ref=context.actor.principal_ref,
            source="control-plane",
        )
        return cast(dict[str, JsonValue], await cp.get_task(context, resource_ref))

    async def _bulk_update_management_command(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        cp = cast(Any, self)
        if resource_ref != "tasks":
            raise ValueError("bulk task-management command resource_ref must be 'tasks'")
        raw_updates = payload.get("updates")
        if not isinstance(raw_updates, list) or not raw_updates:
            raise ValueError("updates must be a non-empty array")
        if len(raw_updates) > 100:
            raise ValueError("bulk task-management updates are limited to 100 items")

        parsed: list[tuple[str, dict[str, JsonValue]]] = []
        for raw_update in raw_updates:
            if not isinstance(raw_update, dict):
                raise ValueError("each bulk update must be an object")
            task_id = raw_update.get("task_id")
            changes = raw_update.get("changes")
            if not isinstance(task_id, str) or not task_id.strip():
                raise ValueError("bulk update task_id must be a non-blank string")
            if not isinstance(changes, dict) or not changes:
                raise ValueError("bulk update changes must be a non-empty object")
            parsed.append((task_id, changes))

        batch_digest = _payload_digest(payload)
        for task_id, _ in parsed:
            task = await cp._kernel.get_task(task_id)
            await cp._authorize_for_task(
                context,
                "task:update-management",
                task_id,
                task,
                request_payload_digest=batch_digest,
            )

        prepared = await cp._task_management.prepare_batch(parsed)
        key = _require_idempotency_key(context)
        applied: list[JsonValue] = []
        for index, update in enumerate(prepared):
            view = await cp._task_management.commit(
                update,
                idempotency_key=f"{key}:task-management:{index}",
                actor_ref=context.actor.principal_ref,
                source="control-plane",
            )
            applied.append({"task_id": update.task.task_id, "eligible": view.eligible})
        return {
            "id": f"bulk:{key}",
            "type": "task-management-bulk-result",
            "atomic": False,
            "authorization_preflighted": True,
            "count": len(applied),
            "items": applied,
        }

    async def create_workspace(
        self,
        context: RequestContext,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        """Keep #37 Workspace creation bound to the original northbound payload."""

        cp = cast(Any, self)
        provider = cp.workspace_provider
        if provider is None:
            return cast(
                dict[str, JsonValue],
                await super().create_workspace(context, payload),  # type: ignore[misc]
            )

        project_id = _required_string(payload, "project_id")
        project = cp._scopes.get_project(project_id)
        await cp._authorize(
            context,
            "workspace:create",
            project_id,
            owner_type=project.owner_ref.type,
            owner_id=project.owner_ref.id,
            project_id=project.id,
            request_payload_digest=_payload_digest(payload),
        )
        key = _require_key(context)
        existing_id = cp._workspace_command_results.get(key)
        if existing_id is not None:
            return _workspace_resource(await provider.get_workspace(existing_id))

        workspace_type = cp._workspace_type_for_payload(payload)
        access_mode = _workspace_access_mode(payload, workspace_type)
        retention = _workspace_retention(payload, workspace_type)
        workspace = await provider.create_workspace(
            project_id=project_id,
            owner_ref=project.owner_ref,
            workspace_type=workspace_type,
            context=_data_access_context(context, project),
            access_mode=access_mode,
            retention=retention,
            source_refs=_workspace_source_refs(payload),
            files=_workspace_files(payload),
            workspace_id=_optional_string(payload, "workspace_id"),
        )
        cp._workspace_command_results[key] = workspace.id
        return _workspace_resource(workspace)

    def _workspace_type_for_payload(self, payload: dict[str, JsonValue]) -> Any:
        # Reuse the canonical parser without duplicating its enum/default rules.
        from ai_multi_agent_platform.workspaces import WorkspaceType

        from .workspace_contract import _enum_field

        return _enum_field(
            payload,
            "workspace_type",
            WorkspaceType,
            WorkspaceType.PERSISTENT_PROJECT,
        )

    async def _resolve_workspace_input(
        self,
        context: RequestContext,
        task_id: str,
        payload: dict[str, JsonValue],
    ) -> tuple[Any, Any]:
        """Bind workspace-use approval to the resolved immutable snapshot."""

        cp = cast(Any, self)
        provider = cp.workspace_provider
        if provider is None:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                "canonical WorkspaceProvider is required for Run workspace binding",
                retryable=True,
            )
        cp._require_binding_repository()
        task = await cp._kernel.get_task(task_id)
        workspace_id = _required_string(payload, "workspace_id")
        workspace = await provider.get_workspace(workspace_id)
        if task.task.project_id is None or workspace.project_id != task.task.project_id:
            raise ContractError(
                ErrorCode.CONFLICT,
                "Run workspace must belong to the same project as the Task",
            )
        snapshot_id = (
            _optional_string(payload, "workspace_snapshot_id") or workspace.base_snapshot_id
        )
        if snapshot_id is None:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "workspace has no canonical base snapshot",
            )
        snapshot = await provider.get_snapshot(snapshot_id)
        if snapshot.workspace_id != workspace.id:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "workspace_snapshot_id belongs to a different workspace",
                details={"field": "workspace_snapshot_id"},
            )
        binding_payload: dict[str, JsonValue] = {
            "workspace_id": workspace.id,
            "workspace_snapshot_id": snapshot.id,
            "workspace_content_checksum": snapshot.content_checksum,
        }
        await cp._authorize(
            context,
            "workspace:use",
            workspace.id,
            owner_type=workspace.owner_ref.type,
            owner_id=workspace.owner_ref.id,
            project_id=workspace.project_id,
            request_payload_digest=_payload_digest(binding_payload),
        )
        return workspace, snapshot

    async def start_task(
        self,
        context: RequestContext,
        task_id: str,
        payload: dict[str, JsonValue] | None = None,
    ) -> dict[str, JsonValue]:
        cp = cast(Any, self)
        if not _has_binding_fields(payload):
            return cast(
                dict[str, JsonValue],
                await super().start_task(context, task_id, payload),  # type: ignore[misc]
            )

        task = await cp._kernel.get_task(task_id)
        workspace, snapshot = await cp._resolve_workspace_input(context, task_id, payload or {})
        binding_payload: dict[str, JsonValue] = {
            "workspace_id": workspace.id,
            "workspace_snapshot_id": snapshot.id,
            "workspace_content_checksum": snapshot.content_checksum,
        }
        await cp._authorize_for_task(
            context,
            "task:start",
            task_id,
            task,
            request_payload_digest=_payload_digest(binding_payload),
        )
        await cp._task_management.require_eligible(task_id)
        key = _require_key(context)
        if task.plan_ref is None:
            await cp._kernel.plan_task(
                idempotency_key=f"{key}:plan",
                task_id=task_id,
                actor_ref=context.actor.principal_ref,
                source="control-plane",
            )
        run = await cp._kernel.create_run(
            idempotency_key=f"{key}:create-run",
            task_id=task_id,
            actor_ref=context.actor.principal_ref,
            source="control-plane",
        )
        await cp._bind_run(run.run_id, task_id, workspace, snapshot)
        await cp._kernel.start_run(
            idempotency_key=f"{key}:start-run",
            task_id=task_id,
            run_id=run.run_id,
            actor_ref=context.actor.principal_ref,
            source="control-plane",
        )
        return cast(
            dict[str, JsonValue],
            await super().get_run(context, run.run_id, task_id=task_id),  # type: ignore[misc]
        )

    async def retry_task(
        self,
        context: RequestContext,
        task_id: str,
        payload: dict[str, JsonValue] | None = None,
    ) -> dict[str, JsonValue]:
        cp = cast(Any, self)
        task = await cp._kernel.get_task(task_id)
        explicit_binding = _has_binding_fields(payload)
        previous_binding = None
        if not explicit_binding and cp._run_workspace_bindings is not None:
            previous_binding = await cp._latest_binding(task.run_ids)

        if not explicit_binding and previous_binding is None:
            return cast(
                dict[str, JsonValue],
                await super().retry_task(context, task_id, payload),  # type: ignore[misc]
            )

        workspace = None
        snapshot = None
        if explicit_binding:
            workspace, snapshot = await cp._resolve_workspace_input(
                context,
                task_id,
                payload or {},
            )
            binding_payload: dict[str, JsonValue] = {
                "workspace_id": workspace.id,
                "workspace_snapshot_id": snapshot.id,
                "workspace_content_checksum": snapshot.content_checksum,
            }
        else:
            assert previous_binding is not None
            binding_payload = {
                "workspace_id": previous_binding.workspace_id,
                "workspace_snapshot_id": previous_binding.workspace_snapshot_id,
                "workspace_content_checksum": previous_binding.content_checksum,
            }

        await cp._authorize_for_task(
            context,
            "task:retry",
            task_id,
            task,
            request_payload_digest=_payload_digest(binding_payload),
        )
        await cp._task_management.require_eligible(task_id)
        key = _require_key(context)
        run = await cp._kernel.retry_task(
            idempotency_key=key,
            task_id=task_id,
            actor_ref=context.actor.principal_ref,
            source="control-plane",
        )
        if workspace is not None and snapshot is not None:
            await cp._bind_run(run.run_id, task_id, workspace, snapshot)
        elif previous_binding is not None:
            await cp._bind_existing_target(run.run_id, task_id, previous_binding)
        return cast(
            dict[str, JsonValue],
            await super().get_run(context, run.run_id, task_id=task_id),  # type: ignore[misc]
        )
