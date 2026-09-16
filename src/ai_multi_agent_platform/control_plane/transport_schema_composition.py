"""Compose generated-client schemas without erasing established OpenAPI fields."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .models import API_VERSION


def preserve_existing_transport_schemas(
    specification: dict[str, Any],
    baseline_schemas: dict[str, Any],
) -> dict[str, Any]:
    """Merge pre-existing public schemas back into generated-client refinements.

    The Control Plane is assembled by multiple contract contributors. Frontend
    generation may refine those schemas, but it must not erase fields owned by
    earlier contributors such as Workspace lifecycle or Run workspace binding.
    """

    schemas = specification.setdefault("components", {}).setdefault("schemas", {})

    generated_workspace = schemas.get("Workspace")
    baseline_workspace = baseline_schemas.get("Workspace")
    if isinstance(generated_workspace, dict) and isinstance(baseline_workspace, dict):
        schemas["WorkspaceTransport"] = deepcopy(generated_workspace)
        schemas["Workspace"] = deepcopy(baseline_workspace)
        _bind_workspace_transport_responses(specification)

    for name, baseline in baseline_schemas.items():
        if name == "Workspace":
            continue
        generated = schemas.get(name)
        if isinstance(baseline, dict) and isinstance(generated, dict):
            _merge_object_schema(generated, baseline)

    return specification


def _merge_object_schema(target: dict[str, Any], baseline: dict[str, Any]) -> None:
    target_properties = target.get("properties")
    baseline_properties = baseline.get("properties")
    if isinstance(target_properties, dict) and isinstance(baseline_properties, dict):
        for name, schema in baseline_properties.items():
            target_properties.setdefault(name, deepcopy(schema))

    target_required = target.get("required")
    baseline_required = baseline.get("required")
    if isinstance(target_required, list) and isinstance(baseline_required, list):
        for name in baseline_required:
            if name not in target_required:
                target_required.append(name)


def _bind_workspace_transport_responses(specification: dict[str, Any]) -> None:
    schemas = specification.setdefault("components", {}).setdefault("schemas", {})
    page = schemas.get("WorkspacePage")
    if isinstance(page, dict):
        all_of = page.get("allOf")
        if isinstance(all_of, list):
            for entry in all_of:
                if not isinstance(entry, dict):
                    continue
                properties = entry.get("properties")
                if not isinstance(properties, dict):
                    continue
                items = properties.get("items")
                if isinstance(items, dict):
                    items["items"] = {"$ref": "#/components/schemas/WorkspaceTransport"}

    prefix = f"/api/{API_VERSION}/workspaces"
    _set_response_schema(specification, prefix, "post", "201", "WorkspaceTransport")
    _set_response_schema(
        specification,
        f"{prefix}/{{workspace_id}}",
        "get",
        "200",
        "WorkspaceTransport",
    )


def _set_response_schema(
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
    if not isinstance(response, dict):
        return
    response["content"] = {
        "application/json": {"schema": {"$ref": f"#/components/schemas/{schema_name}"}}
    }
