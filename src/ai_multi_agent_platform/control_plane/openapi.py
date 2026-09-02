"""Generated OpenAPI 3.1 document for the current Control Plane surface."""

from __future__ import annotations

from typing import Any

from .models import API_VERSION


def build_openapi() -> dict[str, Any]:
    """Return the machine-readable current-scope API contract."""

    paths: dict[str, Any] = {
        f"/api/{API_VERSION}": {
            "get": _operation("getApiManifest", "Control Plane manifest", security=False)
        },
        f"/api/{API_VERSION}/health": {
            "get": _operation("getHealth", "Core health", security=False)
        },
        f"/api/{API_VERSION}/readiness": {
            "get": _operation("getReadiness", "Core readiness", security=False)
        },
        f"/api/{API_VERSION}/openapi.json": {
            "get": _operation("getOpenApi", "OpenAPI specification", security=False)
        },
        f"/api/{API_VERSION}/projects": {
            "get": _list_operation("listProjects", "Project page"),
            "post": _create_operation("createProject", "Created project", "CreateProjectRequest"),
        },
        f"/api/{API_VERSION}/projects/{{project_id}}": {
            "get": _read_operation("getProject", "project_id", "Project")
        },
        f"/api/{API_VERSION}/workspaces": {
            "get": _list_operation("listWorkspaces", "Workspace identity page"),
            "post": _create_operation(
                "createWorkspace",
                "Created workspace identity",
                "CreateWorkspaceRequest",
            ),
        },
        f"/api/{API_VERSION}/workspaces/{{workspace_id}}": {
            "get": _read_operation("getWorkspace", "workspace_id", "Workspace identity")
        },
        f"/api/{API_VERSION}/tasks": {
            "get": _list_operation("listTasks", "Task page"),
            "post": _create_operation("createTask", "Created task", "CreateTaskRequest"),
        },
        f"/api/{API_VERSION}/tasks/{{task_id}}": {
            "get": _read_operation("getTask", "task_id", "Task")
        },
        f"/api/{API_VERSION}/tasks/{{task_id}}/runs": {
            "get": {
                **_list_operation("listTaskRuns", "Run page"),
                "parameters": [_path_parameter("task_id"), *_query_parameters()],
            }
        },
        f"/api/{API_VERSION}/tasks/{{task_id}}/runs/{{run_id}}": {
            "get": {
                **_operation("getTaskRun", "Run"),
                "parameters": [_path_parameter("task_id"), _path_parameter("run_id")],
            }
        },
        f"/api/{API_VERSION}/tasks/{{task_id}}/runs/{{run_id}}:cancel": {
            "post": {
                **_operation("cancelRun", "Cancelled run"),
                "parameters": [
                    _path_parameter("task_id"),
                    _path_parameter("run_id"),
                    {"$ref": "#/components/parameters/IdempotencyKey"},
                ],
            }
        },
        f"/api/{API_VERSION}/runs": {"get": _list_operation("listRuns", "Run page")},
        f"/api/{API_VERSION}/runs/{{run_id}}": {"get": _read_operation("getRun", "run_id", "Run")},
        f"/api/{API_VERSION}/tasks/{{task_id}}/timeline": {
            "get": {
                **_list_operation("getTaskTimeline", "Canonical event page"),
                "parameters": [_path_parameter("task_id"), *_query_parameters()],
            }
        },
        f"/api/{API_VERSION}/tasks/{{task_id}}/events/stream": {
            "get": {
                "operationId": "streamTaskEvents",
                "description": "SSE stream of canonical platform events only.",
                "parameters": [
                    _path_parameter("task_id"),
                    {
                        "name": "after_event_id",
                        "in": "query",
                        "schema": {"type": "string"},
                    },
                ],
                "responses": {
                    "200": {
                        "description": "Canonical Server-Sent Events",
                        "content": {"text/event-stream": {}},
                    },
                    **_error_responses(),
                },
            }
        },
    }

    paths.update(
        {
            f"/api/{API_VERSION}/model-providers": {
                "get": _list_operation("listModelProviders", "Model provider page")
            },
            f"/api/{API_VERSION}/model-providers/{{provider_id}}": {
                "get": _read_operation("getModelProvider", "provider_id", "Model provider")
            },
            f"/api/{API_VERSION}/models": {
                "get": _list_operation("listModels", "Model configuration page")
            },
            f"/api/{API_VERSION}/models/{{model_id}}": {
                "get": _read_operation("getModel", "model_id", "Model configuration")
            },
        }
    )

    for command in ("enable", "disable", "refresh-health"):
        paths[f"/api/{API_VERSION}/model-providers/{{provider_id}}:{command}"] = {
            "post": {
                **_operation(
                    f"{command.replace('-', ' ').title().replace(' ', '')}ModelProvider",
                    "Updated model provider",
                ),
                "parameters": [
                    _path_parameter("provider_id"),
                    {"$ref": "#/components/parameters/IdempotencyKey"},
                ],
            }
        }

    for command in ("enable", "disable"):
        paths[f"/api/{API_VERSION}/models/{{model_id}}:{command}"] = {
            "post": {
                **_operation(f"{command}Model", "Updated model configuration"),
                "parameters": [
                    _path_parameter("model_id"),
                    {"$ref": "#/components/parameters/IdempotencyKey"},
                ],
            }
        }

    for command in ("queue", "start", "cancel", "retry"):
        paths[f"/api/{API_VERSION}/tasks/{{task_id}}:{command}"] = {
            "post": {
                **_operation(f"{command}Task", f"Task {command} command result"),
                "parameters": [
                    _path_parameter("task_id"),
                    {"$ref": "#/components/parameters/IdempotencyKey"},
                ],
            }
        }

    for collection in ("plans", "steps", "artifacts", "results"):
        paths[f"/api/{API_VERSION}/{collection}"] = {
            "get": _list_operation(f"list{collection.title()}", f"{collection} reference page")
        }
        paths[f"/api/{API_VERSION}/{collection}/{{resource_id}}"] = {
            "get": _read_operation(
                f"get{collection.title()}Reference",
                "resource_id",
                f"{collection[:-1]} reference",
            )
        }

    return {
        "openapi": "3.1.0",
        "info": {
            "title": "AI Multi-Agent Platform Control Plane",
            "version": "1.0.0",
            "description": (
                "Small platform-owned northbound API for the canonical resources that exist "
                "at the #32 foundation stage. Later domains extend this API rather than being "
                "predeclared here."
            ),
        },
        "paths": paths,
        "components": {
            "parameters": {
                "IdempotencyKey": {
                    "name": "Idempotency-Key",
                    "in": "header",
                    "required": True,
                    "schema": {"type": "string", "minLength": 1},
                }
            },
            "schemas": _schemas(),
            "responses": {
                "Error": {
                    "description": "Canonical API error",
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/APIError"}}
                    },
                }
            },
        },
        "x-platform-api-version": API_VERSION,
        "x-evolution-policy": {
            "additive_changes": "allowed within v1",
            "breaking_changes": "require a new major path namespace",
            "later_domains": (
                "register new Control Plane paths only when their canonical domain exists"
            ),
            "private_backends": "never become northbound client contracts",
        },
    }


def _operation(
    operation_id: str,
    description: str,
    *,
    security: bool = True,
) -> dict[str, Any]:
    operation: dict[str, Any] = {
        "operationId": operation_id,
        "responses": {"200": {"description": description}, **_error_responses()},
    }
    if not security:
        operation["security"] = []
    return operation


def _list_operation(operation_id: str, description: str) -> dict[str, Any]:
    return {
        **_operation(operation_id, description),
        "parameters": _query_parameters(),
    }


def _create_operation(operation_id: str, description: str, schema: str) -> dict[str, Any]:
    return {
        "operationId": operation_id,
        "parameters": [{"$ref": "#/components/parameters/IdempotencyKey"}],
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": {"$ref": f"#/components/schemas/{schema}"}}},
        },
        "responses": {"201": {"description": description}, **_error_responses()},
    }


def _read_operation(operation_id: str, parameter: str, description: str) -> dict[str, Any]:
    return {
        **_operation(operation_id, description),
        "parameters": [_path_parameter(parameter)],
    }


def _path_parameter(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "in": "path",
        "required": True,
        "schema": {"type": "string"},
    }


def _query_parameters() -> list[dict[str, Any]]:
    return [
        {
            "name": "limit",
            "in": "query",
            "schema": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
        },
        {"name": "cursor", "in": "query", "schema": {"type": "string"}},
        {"name": "sort", "in": "query", "schema": {"type": "string", "default": "id"}},
        {
            "name": "direction",
            "in": "query",
            "schema": {"type": "string", "enum": ["asc", "desc"], "default": "asc"},
        },
        {"name": "q", "in": "query", "schema": {"type": "string"}},
        {"name": "fields", "in": "query", "schema": {"type": "string"}},
        {
            "name": "filter[field]",
            "in": "query",
            "description": (
                "Exact canonical-field filter; replace field with a resource field name."
            ),
            "schema": {"type": "string"},
        },
    ]


def _error_responses() -> dict[str, Any]:
    return {
        status: {"$ref": "#/components/responses/Error"}
        for status in (
            "400",
            "401",
            "403",
            "404",
            "409",
            "413",
            "415",
            "422",
            "429",
            "500",
            "502",
            "503",
            "504",
        )
    }


def _schemas() -> dict[str, Any]:
    owner: dict[str, Any] = {
        "type": "object",
        "required": ["type", "id"],
        "properties": {
            "type": {
                "type": "string",
                "enum": ["user", "organization", "team", "service"],
            },
            "id": {"type": "string", "minLength": 1},
        },
        "additionalProperties": False,
    }
    return {
        "APIError": {
            "type": "object",
            "required": [
                "code",
                "category",
                "message",
                "request_id",
                "correlation_id",
                "retryable",
            ],
            "properties": {
                "code": {"type": "string"},
                "category": {"type": "string"},
                "message": {"type": "string"},
                "request_id": {"type": "string"},
                "correlation_id": {"type": "string"},
                "retryable": {"type": "boolean"},
                "details": {"type": "object", "additionalProperties": True},
                "diagnostics": {"type": "object", "additionalProperties": True},
            },
            "additionalProperties": False,
        },
        "Page": {
            "type": "object",
            "required": ["items", "next_cursor", "total", "limit"],
            "properties": {
                "items": {"type": "array", "items": {"type": "object"}},
                "next_cursor": {"type": ["string", "null"]},
                "total": {"type": "integer", "minimum": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            },
        },
        "Owner": owner,
        "CreateProjectRequest": {
            "type": "object",
            "required": ["name", "owner_type", "owner_id"],
            "properties": {
                "name": {"type": "string", "minLength": 1},
                "owner_type": owner["properties"]["type"],
                "owner_id": {"type": "string", "minLength": 1},
                "project_id": {"type": "string"},
            },
            "additionalProperties": False,
        },
        "CreateWorkspaceRequest": {
            "type": "object",
            "required": ["project_id"],
            "properties": {
                "project_id": {"type": "string", "minLength": 1},
                "workspace_id": {"type": "string"},
            },
            "additionalProperties": False,
        },
        "CreateTaskRequest": {
            "type": "object",
            "required": ["title", "objective", "owner_type", "owner_id"],
            "properties": {
                "title": {"type": "string", "minLength": 1},
                "objective": {"type": "string", "minLength": 1},
                "owner_type": owner["properties"]["type"],
                "owner_id": {"type": "string", "minLength": 1},
                "project_id": {"type": "string"},
                "task_id": {"type": "string"},
            },
            "additionalProperties": False,
        },
    }
