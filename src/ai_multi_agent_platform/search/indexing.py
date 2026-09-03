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
    project_id = _optional_string(resource, "project_id")
    if resource_type == "project":
        project_id = resource_id
    workspace_id = (
        resource_id if resource_type == "workspace" else _optional_string(resource, "workspace_id")
    )
    owner_type, owner_id = _owner(resource)
    status = _status(resource)
    updated_at = _optional_string(resource, "updated_at")
    version = _version(resource)
    canonical_collection = collection or _COLLECTIONS.get(resource_type)
    canonical_ref = (
        f"/api/v1/{canonical_collection}/{resource_id}"
        if canonical_collection is not None
        else None
    )
    tags = _string_sequence(resource, "tags") or _string_sequence(resource, "labels")

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
                *_resource_keywords(resource),
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


def _owner(resource: Mapping[str, JsonValue]) -> tuple[str | None, str | None]:
    for field in ("owner", "owner_ref"):
        value = resource.get(field)
        if not isinstance(value, Mapping):
            continue
        owner_type = value.get("type")
        owner_id = value.get("id")
        if isinstance(owner_type, str) and owner_type.strip() and isinstance(owner_id, str):
            if owner_id.strip():
                return owner_type, owner_id
    return None, None


def _status(resource: Mapping[str, JsonValue]) -> str | None:
    for field in ("status", "effective_health", "health"):
        value = _optional_string(resource, field)
        if value is not None:
            return value
    available = resource.get("available")
    if isinstance(available, bool):
        return "available" if available else "unavailable"
    return None


def _version(resource: Mapping[str, JsonValue]) -> str | None:
    value = resource.get("revision")
    if isinstance(value, int | str):
        return str(value)
    current_revision = resource.get("current_revision")
    if isinstance(current_revision, int | str):
        return str(current_revision)
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
    ):
        value = _optional_string(resource, field)
        if value is not None:
            values.append(value)
    for field in ("aliases", "supported_operations", "modalities", "reasoning"):
        values.extend(_string_sequence(resource, field))
    for field, positive, negative in (
        ("enabled", "enabled", "disabled"),
        ("available", "available", "unavailable"),
    ):
        value = resource.get(field)
        if isinstance(value, bool):
            values.append(positive if value else negative)
    return _deduplicate_strings(tuple(values))


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
