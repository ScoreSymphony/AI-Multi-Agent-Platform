"""Canonical Run API failure inspection for the #32 Control Plane contract."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, cast

from ai_multi_agent_platform.contracts.types import JsonValue

from .extensions import ControlPlane as _ExtensionControlPlane
from .extensions import ControlPlaneHTTP as _ExtensionControlPlaneHTTP
from .extensions import build_openapi as _build_extension_openapi
from .http import HTTPRequest, HTTPResponse
from .models import API_VERSION, PageQuery, RequestContext


class ControlPlane(_ExtensionControlPlane):
    """Current composed Control Plane with a stable canonical Run error view."""

    async def list_runs(
        self,
        context: RequestContext,
        query: PageQuery,
        *,
        task_id: str | None = None,
    ) -> dict[str, JsonValue]:
        base_query = PageQuery(
            limit=query.limit,
            cursor=query.cursor,
            sort=query.sort,
            direction=query.direction,
            search=query.search,
            filters=query.filters,
            fields=(),
        )
        page = await super().list_runs(context, base_query, task_id=task_id)
        items = page.get("items")
        if not isinstance(items, list):
            return page

        decorated_items: list[JsonValue] = []
        for item in items:
            if not isinstance(item, dict):
                decorated_items.append(item)
                continue
            decorated = _decorate_run(item)
            if query.fields:
                wanted = {"id", "type", *query.fields}
                decorated = {name: value for name, value in decorated.items() if name in wanted}
            decorated_items.append(decorated)

        result = dict(page)
        result["items"] = decorated_items
        return result

    async def get_run(
        self,
        context: RequestContext,
        run_id: str,
        *,
        task_id: str | None = None,
    ) -> dict[str, JsonValue]:
        return _decorate_run(await super().get_run(context, run_id, task_id=task_id))

    async def start_task(
        self,
        context: RequestContext,
        task_id: str,
    ) -> dict[str, JsonValue]:
        return _decorate_run(await super().start_task(context, task_id))

    async def retry_task(
        self,
        context: RequestContext,
        task_id: str,
    ) -> dict[str, JsonValue]:
        return _decorate_run(await super().retry_task(context, task_id))

    async def cancel_run(
        self,
        context: RequestContext,
        task_id: str,
        run_id: str,
    ) -> dict[str, JsonValue]:
        return _decorate_run(await super().cancel_run(context, task_id, run_id))


class ControlPlaneHTTP(_ExtensionControlPlaneHTTP):
    """HTTP mapping that publishes the Run error contract in generated OpenAPI."""

    async def handle(self, request: HTTPRequest) -> HTTPResponse:
        response = await super().handle(request)
        if (
            request.method == "GET"
            and request.path.rstrip("/") == f"/api/{API_VERSION}/openapi.json"
            and response.status == 200
            and isinstance(response.body, dict)
        ):
            specification = _augment_openapi(cast(dict[str, Any], deepcopy(response.body)))
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
    """Generate the composed API specification including canonical Run failures."""

    specification = _build_extension_openapi(
        extension_collections=extension_collections,
        extension_commands=extension_commands,
    )
    return _augment_openapi(deepcopy(specification))


def _decorate_run(resource: dict[str, JsonValue]) -> dict[str, JsonValue]:
    decorated = dict(resource)
    decorated["error"] = _canonical_run_error(resource)
    return decorated


def _canonical_run_error(resource: dict[str, JsonValue]) -> dict[str, JsonValue] | None:
    status = resource.get("status")
    if status == "failed":
        return {
            "code": "run_failed",
            "category": "execution",
            "message": _failure_message(resource.get("output")) or "run failed",
            "retryable": False,
        }
    if status == "timed_out":
        return {
            "code": "run_timed_out",
            "category": "timeout",
            "message": _failure_message(resource.get("output")) or "run timed out",
            "retryable": True,
        }
    return None


def _failure_message(output: JsonValue | None) -> str | None:
    if not isinstance(output, dict):
        return None
    for name in ("error", "message", "reason"):
        value = output.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            for nested_name in ("message", "reason"):
                nested = value.get(nested_name)
                if isinstance(nested, str) and nested.strip():
                    return nested.strip()
    return None


def _augment_openapi(specification: dict[str, Any]) -> dict[str, Any]:
    components = specification.setdefault("components", {})
    schemas = components.setdefault("schemas", {})
    schemas["RunError"] = {
        "type": "object",
        "required": ["code", "category", "message", "retryable"],
        "properties": {
            "code": {"type": "string", "enum": ["run_failed", "run_timed_out"]},
            "category": {"type": "string", "enum": ["execution", "timeout"]},
            "message": {"type": "string"},
            "retryable": {"type": "boolean"},
        },
        "additionalProperties": False,
    }
    schemas["Run"] = {
        "type": "object",
        "required": ["id", "type", "task_id", "attempt", "status", "error"],
        "properties": {
            "id": {"type": "string"},
            "type": {"type": "string", "const": "run"},
            "task_id": {"type": "string"},
            "subject_type": {"type": "string", "enum": ["task", "step"]},
            "subject_id": {"type": "string"},
            "attempt": {"type": "integer", "minimum": 1},
            "status": {
                "type": "string",
                "enum": [
                    "queued",
                    "starting",
                    "running",
                    "succeeded",
                    "failed",
                    "cancelled",
                    "timed_out",
                ],
            },
            "project_id": {"type": ["string", "null"]},
            "correlation_id": {"type": "string"},
            "causation_id": {"type": ["string", "null"]},
            "trace_id": {"type": ["string", "null"]},
            "created_at": {"type": "string", "format": "date-time"},
            "updated_at": {"type": "string", "format": "date-time"},
            "started_at": {"type": ["string", "null"], "format": "date-time"},
            "finished_at": {"type": ["string", "null"], "format": "date-time"},
            "output": {"type": "object", "additionalProperties": True},
            "artifact_ids": {"type": "array", "items": {"type": "string"}},
            "result_ids": {"type": "array", "items": {"type": "string"}},
            "recovery_required": {"type": "boolean"},
            "recovery_reason": {"type": ["string", "null"]},
            "error": {
                "oneOf": [
                    {"$ref": "#/components/schemas/RunError"},
                    {"type": "null"},
                ]
            },
        },
        "additionalProperties": False,
    }
    schemas["RunPage"] = {
        "allOf": [
            {"$ref": "#/components/schemas/Page"},
            {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {"$ref": "#/components/schemas/Run"},
                    }
                },
            },
        ]
    }

    _bind_response_schema(specification, f"/api/{API_VERSION}/runs", "get", "RunPage")
    _bind_response_schema(
        specification,
        f"/api/{API_VERSION}/tasks/{{task_id}}/runs",
        "get",
        "RunPage",
    )
    for path in (
        f"/api/{API_VERSION}/runs/{{run_id}}",
        f"/api/{API_VERSION}/tasks/{{task_id}}/runs/{{run_id}}",
    ):
        _bind_response_schema(specification, path, "get", "Run")
    for path in (
        f"/api/{API_VERSION}/tasks/{{task_id}}/runs/{{run_id}}:cancel",
        f"/api/{API_VERSION}/tasks/{{task_id}}:start",
        f"/api/{API_VERSION}/tasks/{{task_id}}:retry",
    ):
        _bind_response_schema(specification, path, "post", "Run")

    specification["x-run-error-contract"] = {
        "failed": "run_failed",
        "timed_out": "run_timed_out",
        "other_statuses": None,
        "source": "canonical run status plus safe human-readable output message/reason when present",
    }
    return specification


def _bind_response_schema(
    specification: dict[str, Any],
    path: str,
    method: str,
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
    response = responses.get("200")
    if not isinstance(response, dict):
        return
    response["content"] = {
        "application/json": {"schema": {"$ref": f"#/components/schemas/{schema_name}"}}
    }
