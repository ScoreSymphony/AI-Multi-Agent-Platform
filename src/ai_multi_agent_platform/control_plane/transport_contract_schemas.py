"""Canonical public Control Plane transport schemas used by generated clients.

This module completes the schema descriptions for stable v1 wire DTOs. It owns
schema shape only; resource lifecycle and serialization remain with their
canonical domain/Control Plane owners.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .models import API_VERSION

_TASK_STATUS = ["draft", "ready", "running", "waiting", "succeeded", "failed", "cancelled"]
_RUN_STATUS = ["queued", "starting", "running", "succeeded", "failed", "cancelled", "timed_out"]
_WORKSPACE_TYPES = [
    "persistent_project",
    "ephemeral_task",
    "isolated_run",
    "read_only_source",
    "cloned",
    "remote",
]
_WORKSPACE_ACCESS = ["read_write", "read_only"]
_WORKSPACE_RETENTION = ["persistent", "ephemeral", "until"]


def augment_transport_schemas(specification: dict[str, Any]) -> dict[str, Any]:
    """Complete stable v1 transport schemas and bind them to public operations."""

    components = specification.setdefault("components", {})
    schemas = components.setdefault("schemas", {})
    schemas.update(_schemas())

    create_project = schemas.get("CreateProjectRequest")
    if isinstance(create_project, dict):
        _merge_properties(
            create_project,
            {
                "project_id": {"type": "string"},
            },
        )

    create_workspace = schemas.get("CreateWorkspaceRequest")
    if isinstance(create_workspace, dict):
        _merge_properties(
            create_workspace,
            {
                "workspace_id": {"type": "string"},
                "workspace_type": {"type": "string", "enum": _WORKSPACE_TYPES},
                "access_mode": {"type": "string", "enum": _WORKSPACE_ACCESS},
                "retention": {"type": "string", "enum": _WORKSPACE_RETENTION},
                "source_refs": {
                    "type": "array",
                    "items": {"$ref": "#/components/schemas/WorkspaceSourceRef"},
                },
                "files": {
                    "type": "array",
                    "items": {"$ref": "#/components/schemas/WorkspaceFileInput"},
                },
            },
        )

    create_task = schemas.get("CreateTaskRequest")
    if isinstance(create_task, dict):
        _merge_properties(create_task, _task_management_properties())

    _bind_foundation_responses(specification)
    specification["x-generated-client-contract"] = {
        "source": "canonical Control Plane OpenAPI components.schemas",
        "version": 1,
        "consumer": "frontend transport DTO generation",
        "ui_models": "not generated; map wire DTOs explicitly at the presentation boundary",
    }
    return specification


_NULLABLE_STRING = {"type": ["string", "null"]}
_TIMESTAMP_OR_NULL = {"type": ["string", "null"], "format": "date-time"}
_JSON_OBJECT = {"type": "object", "additionalProperties": True}
_OWNER_REF = {"$ref": "#/components/schemas/Owner"}

_TRANSPORT_SCHEMAS: dict[str, Any] = {
    "APIManifest": {
        "type": "object",
        "required": ["api_version", "resources", "openapi", "live_updates"],
        "properties": {
            "api_version": {"type": "string"},
            "resources": {"type": "array", "items": {"type": "string"}},
            # Commands remain optional for compatibility with older v1 manifests.
            "commands": {"type": "array", "items": {"type": "string"}},
            "openapi": {"type": "string"},
            "live_updates": {"type": "string"},
        },
        "additionalProperties": True,
    },
    "HealthProviderStatus": {
        "type": "object",
        "required": ["id", "type", "status", "available"],
        "properties": {
            "id": {"type": "string"},
            "type": {"type": "string"},
            "status": {"type": "string"},
            "available": {"type": "boolean"},
        },
        "additionalProperties": False,
    },
    "HealthStatus": {
        "type": "object",
        "required": ["status", "ready", "api_version", "providers"],
        "properties": {
            "status": {"type": "string"},
            "ready": {"type": "boolean"},
            "api_version": {"type": "string"},
            "providers": {
                "type": "array",
                "items": {"$ref": "#/components/schemas/HealthProviderStatus"},
            },
        },
        "additionalProperties": False,
    },
    "Project": {
        "type": "object",
        "required": ["id", "type", "name", "owner", "created_at", "updated_at"],
        "properties": {
            "id": {"type": "string"},
            "type": {"type": "string", "const": "project"},
            "name": {"type": "string"},
            "owner": _OWNER_REF,
            "created_at": {"type": "string", "format": "date-time"},
            "updated_at": {"type": "string", "format": "date-time"},
        },
        "additionalProperties": False,
    },
    "WorkspaceSourceRef": {
        "type": "object",
        "required": ["kind", "ref", "revision", "checksum", "metadata"],
        "properties": {
            "kind": {"type": "string"},
            "ref": {"type": "string"},
            "revision": _NULLABLE_STRING,
            "checksum": _NULLABLE_STRING,
            "metadata": _JSON_OBJECT,
        },
        "additionalProperties": False,
    },
    "WorkspaceFileInput": {
        "type": "object",
        "required": ["relative_path", "file_id", "sha256"],
        "properties": {
            "relative_path": {"type": "string"},
            "file_id": {"type": "string"},
            "sha256": {"type": "string"},
        },
        "additionalProperties": False,
    },
    "IdentityOnlyWorkspace": {
        "type": "object",
        "required": ["id", "type", "project_id", "owner", "created_at", "lifecycle"],
        "properties": {
            "id": {"type": "string"},
            "type": {"type": "string", "const": "workspace"},
            "project_id": {"type": "string"},
            "owner": _OWNER_REF,
            "created_at": _TIMESTAMP_OR_NULL,
            "lifecycle": {"type": "string", "const": "identity_only"},
        },
        "additionalProperties": False,
    },
    "CanonicalWorkspace": {
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
            "source_refs",
            "policy_labels",
            "active_task_ids",
            "active_run_ids",
            "created_at",
            "updated_at",
            "last_used_at",
            "expires_at",
        ],
        "properties": {
            "id": {"type": "string"},
            "type": {"type": "string", "const": "workspace"},
            "project_id": {"type": "string"},
            "owner": _OWNER_REF,
            "lifecycle": {"type": "string", "const": "canonical"},
            "workspace_type": {"type": "string", "enum": _WORKSPACE_TYPES},
            "status": {"type": "string"},
            "access_mode": {"type": "string", "enum": _WORKSPACE_ACCESS},
            "retention": {"type": "string", "enum": _WORKSPACE_RETENTION},
            "revision": {"type": "integer", "minimum": 0},
            "base_snapshot_id": _NULLABLE_STRING,
            "source_refs": {
                "type": "array",
                "items": {"$ref": "#/components/schemas/WorkspaceSourceRef"},
            },
            "policy_labels": {"type": "array", "items": {"type": "string"}},
            "active_task_ids": {"type": "array", "items": {"type": "string"}},
            "active_run_ids": {"type": "array", "items": {"type": "string"}},
            "created_at": {"type": "string", "format": "date-time"},
            "updated_at": {"type": "string", "format": "date-time"},
            "last_used_at": {"type": "string", "format": "date-time"},
            "expires_at": _TIMESTAMP_OR_NULL,
        },
        "additionalProperties": False,
    },
    "Workspace": {
        "oneOf": [
            {"$ref": "#/components/schemas/IdentityOnlyWorkspace"},
            {"$ref": "#/components/schemas/CanonicalWorkspace"},
        ]
    },
    "TaskResponsibility": {
        "type": "object",
        "required": ["kind", "id"],
        "properties": {
            "kind": {"type": "string", "enum": ["user", "team", "organization"]},
            "id": {"type": "string"},
        },
        "additionalProperties": False,
    },
    "AgentAssignment": {
        "type": "object",
        "required": ["kind", "id", "revision", "required", "policy_ref"],
        "properties": {
            "kind": {"type": "string", "enum": ["agent", "agent_team"]},
            "id": {"type": "string"},
            "revision": {"type": ["integer", "null"], "minimum": 1},
            "required": {"type": "boolean"},
            "policy_ref": _NULLABLE_STRING,
        },
        "additionalProperties": False,
    },
    "TaskDependency": {
        "type": "object",
        "required": ["task_id", "kind"],
        "properties": {
            "task_id": {"type": "string"},
            "kind": {"type": "string", "enum": ["depends_on", "related_to"]},
        },
        "additionalProperties": False,
    },
    "Task": {
        "type": "object",
        "required": [
            "id",
            "type",
            "title",
            "objective",
            "status",
            "owner",
            "project_id",
            "revision",
            "plan_ref",
            "step_ids",
            "run_ids",
            "artifact_ids",
            "result_ids",
            "wait_reason",
            "blocked",
            "correlation_id",
            "causation_id",
            "created_at",
            "updated_at",
            "priority",
            "priority_rank",
            "due_at",
            "deadline_timezone",
            "not_before",
            "responsibility",
            "responsible_type",
            "responsible_id",
            "agent_assignment",
            "agent_assignment_type",
            "agent_assignment_id",
            "labels",
            "workspace_id",
            "parent_task_id",
            "dependencies",
            "blocking_reason",
            "effort_hint",
            "resource_hints",
            "archived",
            "hidden",
            "blocking_task_ids",
            "failed_dependency_ids",
            "overdue",
            "not_before_blocked",
            "management_blocked",
            "eligible",
            "effective_blocking_reason",
        ],
        "properties": {
            "id": {"type": "string"},
            "type": {"type": "string", "const": "task"},
            "title": {"type": "string"},
            "objective": {"type": "string"},
            "status": {"type": "string", "enum": _TASK_STATUS},
            "owner": _OWNER_REF,
            "project_id": _NULLABLE_STRING,
            "revision": {"type": "integer", "minimum": 0},
            "plan_ref": _NULLABLE_STRING,
            "step_ids": {"type": "array", "items": {"type": "string"}},
            "run_ids": {"type": "array", "items": {"type": "string"}},
            "artifact_ids": {"type": "array", "items": {"type": "string"}},
            "result_ids": {"type": "array", "items": {"type": "string"}},
            "wait_reason": _NULLABLE_STRING,
            "blocked": {"type": "boolean"},
            "correlation_id": _NULLABLE_STRING,
            "causation_id": _NULLABLE_STRING,
            "created_at": {"type": "string", "format": "date-time"},
            "updated_at": {"type": "string", "format": "date-time"},
            "priority": {"type": "string", "enum": ["low", "normal", "high", "urgent"]},
            "priority_rank": {"type": "integer"},
            "due_at": _TIMESTAMP_OR_NULL,
            "deadline_timezone": _NULLABLE_STRING,
            "not_before": _TIMESTAMP_OR_NULL,
            "responsibility": {
                "oneOf": [
                    {"$ref": "#/components/schemas/TaskResponsibility"},
                    {"type": "null"},
                ]
            },
            "responsible_type": {
                "type": ["string", "null"],
                "enum": ["user", "team", "organization", None],
            },
            "responsible_id": _NULLABLE_STRING,
            "agent_assignment": {
                "oneOf": [
                    {"$ref": "#/components/schemas/AgentAssignment"},
                    {"type": "null"},
                ]
            },
            "agent_assignment_type": {
                "type": ["string", "null"],
                "enum": ["agent", "agent_team", None],
            },
            "agent_assignment_id": _NULLABLE_STRING,
            "labels": {"type": "array", "items": {"type": "string"}},
            "workspace_id": _NULLABLE_STRING,
            "parent_task_id": _NULLABLE_STRING,
            "dependencies": {
                "type": "array",
                "items": {"$ref": "#/components/schemas/TaskDependency"},
            },
            "blocking_reason": _NULLABLE_STRING,
            "effort_hint": {"type": ["number", "null"], "exclusiveMinimum": 0},
            "resource_hints": _JSON_OBJECT,
            "archived": {"type": "boolean"},
            "hidden": {"type": "boolean"},
            "blocking_task_ids": {"type": "array", "items": {"type": "string"}},
            "failed_dependency_ids": {"type": "array", "items": {"type": "string"}},
            "overdue": {"type": "boolean"},
            "not_before_blocked": {"type": "boolean"},
            "management_blocked": {"type": "boolean"},
            "eligible": {"type": "boolean"},
            "effective_blocking_reason": _NULLABLE_STRING,
        },
        "additionalProperties": False,
    },
    "RunError": {
        "type": "object",
        "required": ["code", "category", "message", "retryable"],
        "properties": {
            "code": {"type": "string", "enum": ["run_failed", "run_timed_out"]},
            "category": {"type": "string", "enum": ["execution", "timeout"]},
            "message": {"type": "string"},
            "retryable": {"type": "boolean"},
        },
        "additionalProperties": False,
    },
    "Run": {
        "type": "object",
        "required": [
            "id",
            "type",
            "task_id",
            "subject_type",
            "subject_id",
            "attempt",
            "status",
            "project_id",
            "correlation_id",
            "causation_id",
            "trace_id",
            "created_at",
            "updated_at",
            "started_at",
            "finished_at",
            "output",
            "artifact_ids",
            "result_ids",
            "recovery_required",
            "recovery_reason",
            "error",
        ],
        "properties": {
            "id": {"type": "string"},
            "type": {"type": "string", "const": "run"},
            "task_id": {"type": "string"},
            "subject_type": {"type": "string", "enum": ["task", "step"]},
            "subject_id": {"type": "string"},
            "attempt": {"type": "integer", "minimum": 1},
            "status": {"type": "string", "enum": _RUN_STATUS},
            "project_id": _NULLABLE_STRING,
            "correlation_id": {"type": "string"},
            "causation_id": _NULLABLE_STRING,
            "trace_id": _NULLABLE_STRING,
            "created_at": {"type": "string", "format": "date-time"},
            "updated_at": {"type": "string", "format": "date-time"},
            "started_at": _TIMESTAMP_OR_NULL,
            "finished_at": _TIMESTAMP_OR_NULL,
            "output": _JSON_OBJECT,
            "artifact_ids": {"type": "array", "items": {"type": "string"}},
            "result_ids": {"type": "array", "items": {"type": "string"}},
            "recovery_required": {"type": "boolean"},
            "recovery_reason": _NULLABLE_STRING,
            "error": {
                "oneOf": [
                    {"$ref": "#/components/schemas/RunError"},
                    {"type": "null"},
                ]
            },
        },
        "additionalProperties": False,
    },
    "ModelCapabilities": {
        "type": "object",
        "required": [
            "context_window",
            "tool_calling",
            "structured_output",
            "streaming",
            "modalities",
            "reasoning",
        ],
        "properties": {
            "context_window": {"type": ["integer", "null"], "minimum": 1},
            "tool_calling": {"type": "boolean"},
            "structured_output": {"type": "boolean"},
            "streaming": {"type": "boolean"},
            "modalities": {"type": "array", "items": {"type": "string"}},
            "reasoning": {"type": "array", "items": {"type": "string"}},
        },
        "additionalProperties": False,
    },
    "Model": {
        "type": "object",
        "required": [
            "id",
            "config_id",
            "type",
            "display_name",
            "provider_id",
            "capabilities",
            "revision",
            "aliases",
            "location",
            "node_ref",
            "health",
            "enabled",
            "priority",
            "resource_hints",
            "cost_metadata",
            "adapter_metadata",
            "effective_health",
        ],
        "properties": {
            "id": {"type": "string"},
            "config_id": {"type": "string"},
            "type": {"type": "string", "const": "model"},
            "display_name": {"type": "string"},
            "provider_id": {"type": "string"},
            "capabilities": {"$ref": "#/components/schemas/ModelCapabilities"},
            "revision": {"type": "integer", "minimum": 1},
            "aliases": {"type": "array", "items": {"type": "string"}},
            "location": {"type": "string", "enum": ["local", "self_hosted", "remote"]},
            "node_ref": _NULLABLE_STRING,
            "health": {"type": "string"},
            "enabled": {"type": "boolean"},
            "priority": {"type": "integer"},
            "resource_hints": _JSON_OBJECT,
            "cost_metadata": _JSON_OBJECT,
            "adapter_metadata": {"type": "array", "items": _JSON_OBJECT},
            "effective_health": {"type": "string"},
        },
        "additionalProperties": False,
    },
    "ModelProvider": {
        "type": "object",
        "required": [
            "id",
            "type",
            "provider_type",
            "contract_version",
            "supported_operations",
            "capabilities",
            "health",
            "enabled",
            "available",
            "limits",
            "resources",
            "adapter_metadata",
        ],
        "properties": {
            "id": {"type": "string"},
            "type": {"type": "string", "const": "model-provider"},
            "provider_type": {"type": "string"},
            "contract_version": {"type": "string"},
            "supported_operations": {"type": "array", "items": {"type": "string"}},
            "capabilities": {"type": "array", "items": _JSON_OBJECT},
            "health": {"type": "string"},
            "enabled": {"type": "boolean"},
            "available": {"type": "boolean"},
            "limits": _JSON_OBJECT,
            "resources": _JSON_OBJECT,
            "adapter_metadata": {"type": "array", "items": _JSON_OBJECT},
        },
        "additionalProperties": False,
    },
}


def _schemas() -> dict[str, Any]:
    return deepcopy(_TRANSPORT_SCHEMAS)


def _task_management_properties() -> dict[str, Any]:
    return {
        "task_id": {"type": "string"},
        "priority": {"type": "string", "enum": ["low", "normal", "high", "urgent"]},
        "due_at": {"type": ["string", "null"], "format": "date-time"},
        "deadline_timezone": {"type": ["string", "null"]},
        "not_before": {"type": ["string", "null"], "format": "date-time"},
        "responsibility": {
            "oneOf": [
                {"$ref": "#/components/schemas/TaskResponsibility"},
                {"type": "null"},
            ]
        },
        "agent_assignment": {
            "oneOf": [
                {"$ref": "#/components/schemas/AgentAssignment"},
                {"type": "null"},
            ]
        },
        "labels": {"type": "array", "items": {"type": "string"}},
        "workspace_id": {"type": ["string", "null"]},
        "parent_task_id": {"type": ["string", "null"]},
        "dependencies": {
            "type": "array",
            "items": {"$ref": "#/components/schemas/TaskDependency"},
        },
        "blocking_reason": {"type": ["string", "null"]},
        "effort_hint": {"type": ["number", "null"], "exclusiveMinimum": 0},
        "resource_hints": {"type": "object", "additionalProperties": True},
        "archived": {"type": "boolean"},
        "hidden": {"type": "boolean"},
    }


def _merge_properties(schema: dict[str, Any], properties: dict[str, Any]) -> None:
    existing = schema.setdefault("properties", {})
    if not isinstance(existing, dict):
        raise TypeError("OpenAPI object schema properties must be a mapping")
    existing.update(properties)


def _bind_foundation_responses(specification: dict[str, Any]) -> None:
    prefix = f"/api/{API_VERSION}"
    _bind_response(specification, prefix, "get", "200", "APIManifest")
    _bind_response(specification, f"{prefix}/health", "get", "200", "HealthStatus")
    _bind_response(specification, f"{prefix}/readiness", "get", "200", "HealthStatus")
    _bind_response(specification, f"{prefix}/readiness", "get", "503", "HealthStatus")

    _bind_resource(specification, "projects", "Project", "201")
    _bind_resource(specification, "workspaces", "Workspace", "201")
    _bind_resource(specification, "tasks", "Task", "201")
    _bind_resource(specification, "model-providers", "ModelProvider")
    _bind_resource(specification, "models", "Model")

    for command in ("queue", "cancel"):
        _bind_response(
            specification,
            f"{prefix}/tasks/{{task_id}}:{command}",
            "post",
            "200",
            "Task",
        )
    for command in ("enable", "disable", "refresh-health"):
        _bind_response(
            specification,
            f"{prefix}/model-providers/{{provider_id}}:{command}",
            "post",
            "200",
            "ModelProvider",
        )
    for command in ("enable", "disable"):
        _bind_response(
            specification,
            f"{prefix}/models/{{model_id}}:{command}",
            "post",
            "200",
            "Model",
        )


def _bind_resource(
    specification: dict[str, Any],
    collection: str,
    schema_name: str,
    create_status: str | None = None,
) -> None:
    prefix = f"/api/{API_VERSION}/{collection}"
    page_name = f"{schema_name}Page"
    schemas = specification.setdefault("components", {}).setdefault("schemas", {})
    schemas[page_name] = {
        "allOf": [
            {"$ref": "#/components/schemas/Page"},
            {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {"$ref": f"#/components/schemas/{schema_name}"},
                    }
                },
            },
        ]
    }
    _bind_response(specification, prefix, "get", "200", page_name)
    if create_status is not None:
        _bind_response(specification, prefix, "post", create_status, schema_name)

    parameter = {
        "projects": "project_id",
        "workspaces": "workspace_id",
        "tasks": "task_id",
        "model-providers": "provider_id",
        "models": "model_id",
    }[collection]
    _bind_response(
        specification,
        f"{prefix}/{{{parameter}}}",
        "get",
        "200",
        schema_name,
    )


def _bind_response(
    specification: dict[str, Any],
    path: str,
    method: str,
    status: str,
    schema_name: str,
) -> None:
    paths = specification.get("paths")
    if not isinstance(paths, dict):
        return
    path_item = paths.get(path)
    if not isinstance(path_item, dict):
        return
    operation = path_item.get(method)
    if not isinstance(operation, dict):
        return
    responses = operation.get("responses")
    if not isinstance(responses, dict):
        return
    response = responses.get(status)
    if status == "503" and path.endswith("/readiness"):
        # Readiness uses the health document at both 200 and 503; it is not a
        # canonical API error envelope.
        response = {"description": "Control Plane is not ready"}
        responses[status] = response
    elif not isinstance(response, dict) or "$ref" in response:
        return
    response["content"] = {
        "application/json": {"schema": {"$ref": f"#/components/schemas/{schema_name}"}}
    }
