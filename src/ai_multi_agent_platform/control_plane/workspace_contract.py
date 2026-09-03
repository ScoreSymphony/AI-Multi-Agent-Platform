"""Canonical Workspace integration for the composed Control Plane."""

from __future__ import annotations

from copy import deepcopy
from enum import StrEnum
from typing import Any, cast

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.interfaces import (
    AuthorizationProvider,
    EventProvider,
    ProviderContract,
)
from ai_multi_agent_platform.contracts.types import JsonValue, OperationContext, OperationControl
from ai_multi_agent_platform.data import DataAccessContext
from ai_multi_agent_platform.domain import Project
from ai_multi_agent_platform.kernel import PlatformKernel
from ai_multi_agent_platform.kernel.repository import EventRepository
from ai_multi_agent_platform.models import ModelRegistry
from ai_multi_agent_platform.workspaces import (
    Workspace,
    WorkspaceAccessMode,
    WorkspaceFile,
    WorkspaceProvider,
    WorkspaceRetention,
    WorkspaceSourceKind,
    WorkspaceSourceRef,
    WorkspaceType,
)

from .http import HTTPRequest, HTTPResponse
from .models import API_VERSION, PageQuery, RequestContext, paginate
from .observability_contract import ControlPlane as _ObservabilityControlPlane
from .observability_contract import ControlPlaneHTTP as _ObservabilityControlPlaneHTTP
from .observability_contract import build_openapi as _build_observability_openapi
from .service import ScopeStore, _optional_string, _require_key, _required_string


class ControlPlane(_ObservabilityControlPlane):
    """Composed Control Plane with an optional canonical #37 WorkspaceProvider."""

    def __init__(
        self,
        *,
        kernel: PlatformKernel,
        events: EventRepository,
        scopes: ScopeStore | None = None,
        authorization: AuthorizationProvider | None = None,
        live_events: EventProvider | None = None,
        health_providers: tuple[ProviderContract, ...] = (),
        model_registry: ModelRegistry | None = None,
        workspace_provider: WorkspaceProvider | None = None,
    ) -> None:
        super().__init__(
            kernel=kernel,
            events=events,
            scopes=scopes,
            authorization=authorization,
            live_events=live_events,
            health_providers=health_providers,
            model_registry=model_registry,
        )
        self._workspace_provider = workspace_provider
        self._workspace_command_results: dict[str, str] = {}

    @property
    def workspace_provider(self) -> WorkspaceProvider | None:
        return self._workspace_provider

    async def create_workspace(
        self,
        context: RequestContext,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        provider = self._workspace_provider
        if provider is None:
            return await super().create_workspace(context, payload)

        project_id = _required_string(payload, "project_id")
        project = self._scopes.get_project(project_id)
        await self._authorize(
            context,
            "workspace:create",
            project_id,
            owner_type=project.owner_ref.type,
            owner_id=project.owner_ref.id,
            project_id=project.id,
        )
        key = _require_key(context)
        existing_id = self._workspace_command_results.get(key)
        if existing_id is not None:
            return _workspace_resource(await provider.get_workspace(existing_id))

        workspace_type = _enum_field(
            payload,
            "workspace_type",
            WorkspaceType,
            WorkspaceType.PERSISTENT_PROJECT,
        )
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
        self._workspace_command_results[key] = workspace.id
        return _workspace_resource(workspace)

    async def list_workspaces(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> dict[str, JsonValue]:
        provider = self._workspace_provider
        if provider is None:
            return await super().list_workspaces(context, query)

        await self._authorize(context, "workspace:list", "workspaces")
        resources: list[dict[str, JsonValue]] = []
        for workspace in await provider.list_workspaces():
            if await self._allowed(
                context,
                "workspace:list",
                workspace.id,
                owner_type=workspace.owner_ref.type,
                owner_id=workspace.owner_ref.id,
                project_id=workspace.project_id,
            ):
                resources.append(_workspace_resource(workspace))
        return paginate(resources, query)

    async def get_workspace(
        self,
        context: RequestContext,
        workspace_id: str,
    ) -> dict[str, JsonValue]:
        provider = self._workspace_provider
        if provider is None:
            return await super().get_workspace(context, workspace_id)

        workspace = await provider.get_workspace(workspace_id)
        await self._authorize(
            context,
            "workspace:read",
            workspace.id,
            owner_type=workspace.owner_ref.type,
            owner_id=workspace.owner_ref.id,
            project_id=workspace.project_id,
        )
        return _workspace_resource(workspace)


class ControlPlaneHTTP(_ObservabilityControlPlaneHTTP):
    """HTTP mapping that publishes the canonical Workspace schema."""

    async def handle(self, request: HTTPRequest) -> HTTPResponse:
        response = await super().handle(request)
        if (
            request.method == "GET"
            and request.path.rstrip("/") == f"/api/{API_VERSION}/openapi.json"
            and response.status == 200
            and isinstance(response.body, dict)
        ):
            specification = _augment_workspace_openapi(
                cast(dict[str, Any], deepcopy(response.body))
            )
            return HTTPResponse(
                status=response.status,
                body=cast(dict[str, JsonValue], specification),
                headers=dict(response.headers),
            )
        return response


def build_openapi(
    *,
    extension_collections: tuple[str, ...] = (),
    extension_commands: tuple[str, ...] = (),
) -> dict[str, Any]:
    return _augment_workspace_openapi(
        deepcopy(
            _build_observability_openapi(
                extension_collections=extension_collections,
                extension_commands=extension_commands,
            )
        )
    )


def _data_access_context(context: RequestContext, project: Project) -> DataAccessContext:
    return DataAccessContext(
        operation=OperationContext(
            correlation_id=context.correlation_id,
            owner_type=project.owner_ref.type,
            owner_id=project.owner_ref.id,
            project_id=project.id,
            control=OperationControl(idempotency_key=context.idempotency_key),
        ),
        actor_ref=context.actor.principal_ref,
    )


def _enum_field[EnumT: StrEnum](
    payload: dict[str, JsonValue],
    name: str,
    enum_type: type[EnumT],
    default: EnumT,
) -> EnumT:
    value = payload.get(name)
    if value is None:
        return default
    if not isinstance(value, str):
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"{name} must be a string",
            details={"field": name},
        )
    try:
        return enum_type(value)
    except ValueError as exc:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"unsupported {name}: {value}",
            details={"field": name},
        ) from exc


def _workspace_access_mode(
    payload: dict[str, JsonValue],
    workspace_type: WorkspaceType,
) -> WorkspaceAccessMode:
    default = (
        WorkspaceAccessMode.READ_ONLY
        if workspace_type is WorkspaceType.READ_ONLY_SOURCE
        else WorkspaceAccessMode.READ_WRITE
    )
    return _enum_field(payload, "access_mode", WorkspaceAccessMode, default)


def _workspace_retention(
    payload: dict[str, JsonValue],
    workspace_type: WorkspaceType,
) -> WorkspaceRetention:
    ephemeral = workspace_type in {
        WorkspaceType.EPHEMERAL_TASK,
        WorkspaceType.ISOLATED_RUN,
    }
    default = WorkspaceRetention.EPHEMERAL if ephemeral else WorkspaceRetention.PERSISTENT
    return _enum_field(payload, "retention", WorkspaceRetention, default)


def _workspace_files(payload: dict[str, JsonValue]) -> tuple[WorkspaceFile, ...]:
    value = payload.get("files")
    if value is None:
        return ()
    entries = _object_list(value, "files")
    return tuple(
        WorkspaceFile(
            relative_path=_mapping_string(entry, "relative_path", f"files[{index}]"),
            file_id=_mapping_string(entry, "file_id", f"files[{index}]"),
            sha256=_mapping_string(entry, "sha256", f"files[{index}]"),
        )
        for index, entry in enumerate(entries)
    )


def _workspace_source_refs(payload: dict[str, JsonValue]) -> tuple[WorkspaceSourceRef, ...]:
    value = payload.get("source_refs")
    if value is None:
        return ()
    entries = _object_list(value, "source_refs")
    refs: list[WorkspaceSourceRef] = []
    for index, entry in enumerate(entries):
        parent = f"source_refs[{index}]"
        kind_value = _mapping_string(entry, "kind", parent)
        try:
            kind = WorkspaceSourceKind(kind_value)
        except ValueError as exc:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                f"unsupported workspace source kind: {kind_value}",
                details={"field": f"{parent}.kind"},
            ) from exc
        metadata = entry.get("metadata", {})
        if not isinstance(metadata, dict):
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "workspace source metadata must be an object",
                details={"field": f"{parent}.metadata"},
            )
        refs.append(
            WorkspaceSourceRef(
                kind=kind,
                ref=_mapping_string(entry, "ref", parent),
                revision=_mapping_optional_string(entry, "revision", parent),
                checksum=_mapping_optional_string(entry, "checksum", parent),
                metadata=metadata,
            )
        )
    return tuple(refs)


def _object_list(value: JsonValue, name: str) -> list[dict[str, JsonValue]]:
    if not isinstance(value, list):
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"{name} must be an array",
            details={"field": name},
        )
    result: list[dict[str, JsonValue]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                f"{name} entries must be objects",
                details={"field": f"{name}[{index}]"},
            )
        result.append(item)
    return result


def _mapping_string(value: dict[str, JsonValue], name: str, parent: str) -> str:
    raw = value.get(name)
    if not isinstance(raw, str) or not raw.strip():
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"{parent}.{name} must be a non-blank string",
            details={"field": f"{parent}.{name}"},
        )
    return raw


def _mapping_optional_string(
    value: dict[str, JsonValue],
    name: str,
    parent: str,
) -> str | None:
    raw = value.get(name)
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"{parent}.{name} must be a non-blank string",
            details={"field": f"{parent}.{name}"},
        )
    return raw


def _workspace_resource(workspace: Workspace) -> dict[str, JsonValue]:
    source_refs: list[JsonValue] = [
        {
            "kind": source.kind.value,
            "ref": source.ref,
            "revision": source.revision,
            "checksum": source.checksum,
            "metadata": dict(source.metadata),
        }
        for source in workspace.source_refs
    ]
    return {
        "id": workspace.id,
        "type": "workspace",
        "project_id": workspace.project_id,
        "owner": {"type": workspace.owner_ref.type, "id": workspace.owner_ref.id},
        "lifecycle": "canonical",
        "workspace_type": workspace.workspace_type.value,
        "status": workspace.status.value,
        "access_mode": workspace.access_mode.value,
        "retention": workspace.retention.value,
        "revision": workspace.revision,
        "base_snapshot_id": workspace.base_snapshot_id,
        "source_refs": source_refs,
        "policy_labels": list(workspace.policy_labels),
        "active_task_ids": list(workspace.active_task_ids),
        "active_run_ids": list(workspace.active_run_ids),
        "created_at": workspace.created_at.isoformat(),
        "updated_at": workspace.updated_at.isoformat(),
        "last_used_at": workspace.last_used_at.isoformat(),
        "expires_at": workspace.expires_at.isoformat() if workspace.expires_at else None,
    }


def _augment_workspace_openapi(specification: dict[str, Any]) -> dict[str, Any]:
    schemas = specification.setdefault("components", {}).setdefault("schemas", {})
    schemas["Workspace"] = {
        "type": "object",
        "required": [
            "id",
            "type",
            "project_id",
            "owner",
            "lifecycle",
            "workspace_type",
            "status",
            "access_mode",
            "retention",
            "revision",
            "base_snapshot_id",
        ],
        "properties": {
            "id": {"type": "string"},
            "type": {"type": "string", "const": "workspace"},
            "project_id": {"type": "string"},
            "owner": {"type": "object", "additionalProperties": True},
            "lifecycle": {"type": "string", "enum": ["canonical", "identity_only"]},
            "workspace_type": {
                "type": "string",
                "enum": [item.value for item in WorkspaceType],
            },
            "status": {"type": "string"},
            "access_mode": {
                "type": "string",
                "enum": [item.value for item in WorkspaceAccessMode],
            },
            "retention": {
                "type": "string",
                "enum": [item.value for item in WorkspaceRetention],
            },
            "revision": {"type": "integer", "minimum": 0},
            "base_snapshot_id": {"type": ["string", "null"]},
            "source_refs": {"type": "array", "items": {"type": "object"}},
            "policy_labels": {"type": "array", "items": {"type": "string"}},
            "active_task_ids": {"type": "array", "items": {"type": "string"}},
            "active_run_ids": {"type": "array", "items": {"type": "string"}},
            "created_at": {"type": "string", "format": "date-time"},
            "updated_at": {"type": "string", "format": "date-time"},
            "last_used_at": {"type": "string", "format": "date-time"},
            "expires_at": {"type": ["string", "null"], "format": "date-time"},
        },
        "additionalProperties": False,
    }
    return specification
