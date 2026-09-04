"""Helpers for rebuilding derived search documents from canonical API resources."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

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


def document_from_resource(
    resource: Mapping[str, JsonValue],
    *,
    collection: str | None = None,
) -> SearchDocument:
    """Convert one safe canonical northbound resource into an index document.

    ``collection`` is supplied for explicitly registered Control Plane extensions so
    Search can retain their canonical API route without teaching the core index mapper
    about every future domain.
    """

    resource_type = _required_string(resource, "type")
    resource_id = _required_string(resource, "id")
    title = _display_title(resource_type, resource_id, resource)
    summary = _summary(resource_type, resource)
    project_id = _project_id(resource_type, resource_id, resource)
    workspace_id = _workspace_id(resource_type, resource_id, resource)
    owner_type, owner_id = _owner(resource)
    status = _status(resource)
    updated_at = _updated_at(resource_type, resource)
    version = _version(resource_type, resource)
    canonical_collection = collection or _COLLECTIONS.get(resource_type)
    canonical_ref = (
        f"/api/v1/{canonical_collection}/{resource_id}"
        if canonical_collection is not None
        else None
    )
    tags = _string_sequence(resource, "tags") or _string_sequence(resource, "labels")
    priority = _optional_string(resource, "priority")
    due_at = _optional_string(resource, "due_at")
    responsible_id = _optional_string(resource, "responsible_id")
    agent_assignment_id = _optional_string(resource, "agent_assignment_id")
    blocked = _optional_bool(resource, "blocked")
    overdue = _optional_bool(resource, "overdue")
    dependency_ids = _dependency_ids(resource)

    keywords = _deduplicate_strings(
        tuple(
            value
            for value in (
                resource_type,
                resource_id,
                project_id,
                workspace_id,
                owner_type,
                owner_id,
                status,
                priority,
                due_at,
                responsible_id,
                agent_assignment_id,
                *_resource_keywords(resource),
                *dependency_ids,
                *_profile_keywords(resource),
            )
            if value is not None
        )
    )
    provenance: dict[str, JsonValue] = {"indexed_from": "canonical-control-plane"}
    if collection is not None:
        provenance["collection"] = collection
    resource_provider_id = _optional_string(resource, "provider_id")
    if resource_provider_id is not None:
        provenance["resource_provider_id"] = resource_provider_id
    return SearchDocument(
        resource_type=resource_type,
        resource_id=resource_id,
        title=title,
        summary=summary,
        project_id=project_id,
        workspace_id=workspace_id,
        owner_type=owner_type,
        owner_id=owner_id,
        status=status,
        tags=tags,
        keywords=keywords,
        version=version,
        updated_at=updated_at,
        canonical_ref=canonical_ref,
        provenance=provenance,
        priority=priority,
        due_at=due_at,
        responsible_id=responsible_id,
        agent_assignment_id=agent_assignment_id,
        blocked=blocked,
        overdue=overdue,
        dependency_ids=dependency_ids,
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
    profile = _revision_profile(resource)
    if profile is not None:
        for field in ("name", "title", "display_name", "alias"):
            value = _optional_string(profile, field)
            if value is not None:
                return value
    if resource_type == "run":
        subject_type = _optional_string(resource, "subject_type")
        subject_id = _optional_string(resource, "subject_id")
        if subject_type is not None and subject_id is not None:
            return f"Run for {subject_type} {subject_id}"
    if resource_type == "event":
        event_type = _optional_string(resource, "event_type")
        subject_type = _optional_string(resource, "subject_type")
        subject_id = _optional_string(resource, "subject_id")
        if event_type is not None and subject_type is not None and subject_id is not None:
            return f"{event_type} for {subject_type} {subject_id}"
        if event_type is not None:
            return f"Event {event_type}"
    if resource_type == "approval":
        subject_type = _optional_string(resource, "subject_type")
        subject_id = _optional_string(resource, "subject_id")
        if subject_type is not None and subject_id is not None:
            return f"Approval for {subject_type} {subject_id}"
    if resource_type == "evaluation-run":
        suite_id = _optional_string(resource, "suite_id")
        suite_version = _optional_string(resource, "suite_version")
        if suite_id is not None and suite_version is not None:
            return f"Evaluation run for {suite_id} {suite_version}"
        if suite_id is not None:
            return f"Evaluation run for {suite_id}"
    if resource_type == "usage-aggregate":
        metric_type = _optional_string(resource, "metric_type")
        unit = _optional_string(resource, "unit")
        if metric_type is not None and unit is not None:
            return f"{metric_type} usage ({unit})"
    if resource_type == "usage-budget":
        metric_type = _optional_string(resource, "metric_type")
        scope_type = _optional_string(resource, "scope_type")
        scope_id = _optional_string(resource, "scope_id")
        if metric_type is not None and scope_type is not None and scope_id is not None:
            return f"{metric_type} budget for {scope_type} {scope_id}"
    return f"{resource_type.replace('-', ' ').replace('_', ' ').title()} {resource_id}"


def _summary(resource_type: str, resource: Mapping[str, JsonValue]) -> str:
    for field in ("summary", "objective", "description"):
        value = _optional_string(resource, field)
        if value is not None:
            return value[:500]
    profile = _revision_profile(resource)
    if profile is not None:
        description = _optional_string(profile, "description")
        if description is not None:
            return description[:500]
        role = _optional_string(profile, "role")
        if role is not None:
            return role[:500]
    if resource_type == "run":
        status = _optional_string(resource, "status")
        attempt = resource.get("attempt")
        if status is not None and isinstance(attempt, int):
            return f"Run attempt {attempt} is {status}."
    return ""


def _project_id(
    resource_type: str,
    resource_id: str,
    resource: Mapping[str, JsonValue],
) -> str | None:
    if resource_type == "project":
        return resource_id
    project_id = _optional_string(resource, "project_id")
    if project_id is not None:
        return project_id
    scope = _nested_mapping(resource, "scope")
    if scope is not None:
        project_id = _optional_string(scope, "project_id")
        if project_id is not None:
            return project_id
    if _optional_string(resource, "scope_type") == "project":
        return _optional_string(resource, "scope_id")
    return None


def _workspace_id(
    resource_type: str,
    resource_id: str,
    resource: Mapping[str, JsonValue],
) -> str | None:
    if resource_type == "workspace":
        return resource_id
    workspace_id = _optional_string(resource, "workspace_id")
    if workspace_id is not None:
        return workspace_id
    scope = _nested_mapping(resource, "scope")
    if scope is not None:
        workspace_id = _optional_string(scope, "workspace_id")
        if workspace_id is not None:
            return workspace_id
    if _optional_string(resource, "scope_type") == "workspace":
        return _optional_string(resource, "scope_id")
    return None


def _owner(resource: Mapping[str, JsonValue]) -> tuple[str | None, str | None]:
    for field in ("owner", "owner_ref"):
        value = resource.get(field)
        if isinstance(value, Mapping):
            owner_type = value.get("type")
            owner_id = value.get("id")
            if isinstance(owner_type, str) and owner_type.strip() and isinstance(owner_id, str):
                if owner_id.strip():
                    return owner_type, owner_id
            continue
        if field == "owner_ref" and isinstance(value, str) and ":" in value:
            owner_type, owner_id = value.split(":", 1)
            if owner_type.strip() and owner_id.strip():
                return owner_type, owner_id

    owner_type = _optional_string(resource, "owner_type")
    owner_id = _optional_string(resource, "owner_id")
    if owner_type is not None and owner_id is not None:
        return owner_type, owner_id

    scope = _nested_mapping(resource, "scope")
    if scope is not None:
        owner_type = _optional_string(scope, "owner_type")
        owner_id = _optional_string(scope, "owner_id")
        if owner_type is not None and owner_id is not None:
            return owner_type, owner_id
    return None, None


def _status(resource: Mapping[str, JsonValue]) -> str | None:
    for field in ("status", "state", "effective_health", "health"):
        value = _optional_string(resource, field)
        if value is not None:
            return value
    available = resource.get("available")
    if isinstance(available, bool):
        return "available" if available else "unavailable"
    return None


def _updated_at(resource_type: str, resource: Mapping[str, JsonValue]) -> str | None:
    updated_at = _optional_string(resource, "updated_at")
    if updated_at is not None:
        return updated_at
    if resource_type == "evaluation-run":
        return _optional_string(resource, "completed_at") or _optional_string(
            resource, "started_at"
        )
    return None


def _version(resource_type: str, resource: Mapping[str, JsonValue]) -> str | None:
    for field in ("revision", "current_revision", "plugin_version"):
        value = resource.get(field)
        if isinstance(value, int | str):
            return str(value)
    if resource_type == "evaluation-suite":
        return _optional_string(resource, "version")
    return None


def _revision_profile(resource: Mapping[str, JsonValue]) -> Mapping[str, JsonValue] | None:
    revision = resource.get("revision")
    if not isinstance(revision, Mapping):
        return None
    profile = revision.get("profile")
    if not isinstance(profile, Mapping):
        return None
    return profile


def _profile_keywords(resource: Mapping[str, JsonValue]) -> tuple[str, ...]:
    profile = _revision_profile(resource)
    if profile is None:
        return ()
    return tuple(
        value
        for field in ("name", "role", "description")
        if (value := _optional_string(profile, field)) is not None
    )


def _resource_keywords(resource: Mapping[str, JsonValue]) -> tuple[str, ...]:
    values: list[str] = []
    for field in (
        "provider_id",
        "provider_type",
        "location",
        "health",
        "effective_health",
        "task_id",
        "run_id",
        "plan_id",
        "automation_id",
        "trigger_type",
        "source",
        "event_type",
        "subject_type",
        "subject_id",
        "action",
        "resource_type",
        "resource_id",
        "risk",
        "capability_ref",
        "responsible_type",
        "responsible_id",
        "agent_assignment_type",
        "agent_assignment_id",
        "parent_task_id",
        "effective_blocking_reason",
        "content_type",
        "author",
        "plugin_version",
        "manifest_version",
        "compatibility",
        "install_source",
        "provenance_license",
        "metric_type",
        "unit",
        "aggregation_mode",
        "scope_type",
        "scope_id",
        "kind",
        "threshold_level",
        "window_mode",
        "suite_id",
        "suite_version",
        "baseline_run_id",
    ):
        value = _optional_string(resource, field)
        if value is not None:
            values.append(value)
    for field in (
        "aliases",
        "supported_operations",
        "modalities",
        "reasoning",
        "step_ids",
        "blocking_task_ids",
        "failed_dependency_ids",
        "artifact_ids",
        "capabilities",
        "extension_ids",
        "extension_types",
        "requested_permissions",
        "granted_permissions",
        "dependencies",
    ):
        values.extend(_string_sequence(resource, field))
    for field, positive, negative in (
        ("enabled", "enabled", "disabled"),
        ("available", "available", "unavailable"),
        ("blocked", "blocked", "unblocked"),
        ("overdue", "overdue", "not-overdue"),
        ("archived", "archived", "active"),
        ("hidden", "hidden", "visible"),
        ("eligible", "eligible", "ineligible"),
        ("configured", "configured", "unconfigured"),
    ):
        boolean_value = resource.get(field)
        if isinstance(boolean_value, bool):
            values.append(positive if boolean_value else negative)
    return _deduplicate_strings(tuple(values))


def _dependency_ids(resource: Mapping[str, JsonValue]) -> tuple[str, ...]:
    raw_dependencies = resource.get("dependencies")
    if not isinstance(raw_dependencies, Sequence) or isinstance(
        raw_dependencies, str | bytes | bytearray
    ):
        return ()
    dependency_ids: list[str] = []
    for raw_dependency in raw_dependencies:
        if not isinstance(raw_dependency, Mapping):
            continue
        task_id = raw_dependency.get("task_id")
        if isinstance(task_id, str) and task_id.strip() and task_id not in dependency_ids:
            dependency_ids.append(task_id)
    return tuple(dependency_ids)


def _nested_mapping(
    resource: Mapping[str, JsonValue],
    field: str,
) -> Mapping[str, JsonValue] | None:
    value = resource.get(field)
    return value if isinstance(value, Mapping) else None


def _string_sequence(resource: Mapping[str, JsonValue], field: str) -> tuple[str, ...]:
    value = resource.get(field)
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return ()
    return tuple(item.strip() for item in value if isinstance(item, str) and item.strip())


def _deduplicate_strings(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


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


def _optional_bool(resource: Mapping[str, JsonValue], field: str) -> bool | None:
    value = resource.get(field)
    return value if isinstance(value, bool) else None
