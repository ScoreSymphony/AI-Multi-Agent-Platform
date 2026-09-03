"""Helpers for rebuilding derived search documents from canonical API resources."""

from __future__ import annotations

from collections.abc import Mapping

from ai_multi_agent_platform.contracts.types import JsonValue

from .models import SearchDocument

_COLLECTIONS = {
    "project": "projects",
    "workspace": "workspaces",
    "task": "tasks",
    "run": "runs",
    "plan": "plans",
    "step": "steps",
    "artifact": "artifacts",
    "result": "results",
    "model-provider": "model-providers",
    "model": "models",
}


def document_from_resource(resource: Mapping[str, JsonValue]) -> SearchDocument:
    """Convert one safe canonical northbound resource into an index document."""

    resource_type = _required_string(resource, "type")
    resource_id = _required_string(resource, "id")
    title = _display_title(resource_type, resource_id, resource)
    summary = _summary(resource_type, resource)
    project_id = _optional_string(resource, "project_id")
    if resource_type == "project":
        project_id = resource_id
    workspace_id = resource_id if resource_type == "workspace" else _optional_string(
        resource, "workspace_id"
    )
    status = _optional_string(resource, "status")
    updated_at = _optional_string(resource, "updated_at")
    version = _version(resource)
    collection = _COLLECTIONS.get(resource_type)
    canonical_ref = f"/api/v1/{collection}/{resource_id}" if collection is not None else None

    keywords = tuple(
        value
        for value in (
            resource_type,
            resource_id,
            project_id,
            workspace_id,
            status,
        )
        if value is not None
    )
    return SearchDocument(
        resource_type=resource_type,
        resource_id=resource_id,
        title=title,
        summary=summary,
        project_id=project_id,
        workspace_id=workspace_id,
        status=status,
        keywords=keywords,
        version=version,
        updated_at=updated_at,
        canonical_ref=canonical_ref,
        provenance={"indexed_from": "canonical-control-plane"},
    )


def _display_title(
    resource_type: str,
    resource_id: str,
    resource: Mapping[str, JsonValue],
) -> str:
    for field in ("title", "name", "display_name", "alias"):
        value = _optional_string(resource, field)
        if value is not None:
            return value
    if resource_type == "run":
        subject_type = _optional_string(resource, "subject_type")
        subject_id = _optional_string(resource, "subject_id")
        if subject_type is not None and subject_id is not None:
            return f"Run for {subject_type} {subject_id}"
    return f"{resource_type.replace('-', ' ').title()} {resource_id}"


def _summary(resource_type: str, resource: Mapping[str, JsonValue]) -> str:
    for field in ("summary", "objective", "description"):
        value = _optional_string(resource, field)
        if value is not None:
            return value[:500]
    if resource_type == "run":
        status = _optional_string(resource, "status")
        attempt = resource.get("attempt")
        if status is not None and isinstance(attempt, int):
            return f"Run attempt {attempt} is {status}."
    return ""


def _version(resource: Mapping[str, JsonValue]) -> str | None:
    value = resource.get("revision")
    if isinstance(value, int | str):
        return str(value)
    return None


def _required_string(resource: Mapping[str, JsonValue], field: str) -> str:
    value = _optional_string(resource, field)
    if value is None:
        raise ValueError(f"searchable resource requires non-blank {field}")
    return value


def _optional_string(resource: Mapping[str, JsonValue], field: str) -> str | None:
    value = resource.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        return None
    return value
