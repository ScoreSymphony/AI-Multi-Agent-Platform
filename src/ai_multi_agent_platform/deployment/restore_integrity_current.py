"""Restore-integrity checks for durable stores added after the original #40 closure."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from ai_multi_agent_platform.backup.integrity import RestoreValidationError
from ai_multi_agent_platform.contracts import ContractError
from ai_multi_agent_platform.kernel import RecoveryReport
from ai_multi_agent_platform.templates import JsonTemplateRepository

from .single_node import SingleNodeDeployment

DeploymentRestoreValidator = Callable[[tuple[RecoveryReport, ...]], Awaitable[tuple[str, ...]]]


@dataclass(frozen=True, slots=True)
class _CurrentIndex:
    task_ids: frozenset[str]
    run_owner: dict[str, str]
    project_ids: frozenset[str]
    workspace_projects: dict[str, str]
    file_ids: frozenset[str]
    artifact_ids: frozenset[str]
    result_ids: frozenset[str]
    user_ids: frozenset[str]
    automation_ids: frozenset[str]
    verification_ids: frozenset[str]
    event_ids: frozenset[str]


def single_node_current_restore_integrity_validators(
    deployment: SingleNodeDeployment,
) -> tuple[DeploymentRestoreValidator, ...]:
    """Return restore validators for the current single-node durable composition."""

    async def current(reports: tuple[RecoveryReport, ...]) -> tuple[str, ...]:
        index = await _build_index(deployment, reports)
        notification_count = _validate_notifications(deployment, index)
        template_count = _validate_templates(deployment, index)
        return (f"notification-template-references:{notification_count}:{template_count}",)

    return (current,)


async def _build_index(
    deployment: SingleNodeDeployment,
    reports: tuple[RecoveryReport, ...],
) -> _CurrentIndex:
    task_ids: set[str] = set()
    run_owner: dict[str, str] = {}
    artifact_ids: set[str] = set()
    result_ids: set[str] = set()
    for report in reports:
        try:
            task = await deployment.kernel.get_task(report.task_id)
        except ContractError as exc:
            raise RestoreValidationError(
                f"current restore validator cannot reconstruct task {report.task_id}"
            ) from exc
        task_ids.add(task.task_id)
        artifact_ids.update(task.artifact_ids)
        result_ids.update(task.result_ids)
        for run_id in task.run_ids:
            try:
                run = await deployment.kernel.get_run(task.task_id, run_id)
            except ContractError as exc:
                raise RestoreValidationError(
                    f"current restore validator cannot reconstruct run {run_id}"
                ) from exc
            existing = run_owner.get(run_id)
            if existing is not None and existing != task.task_id:
                raise RestoreValidationError(
                    f"canonical run {run_id} is referenced by multiple tasks"
                )
            run_owner[run_id] = task.task_id
            artifact_ids.update(run.artifact_ids)
            result_ids.update(run.result_ids)

    try:
        automations = await deployment.control_plane.automation_service.list_automations()
    except (ContractError, ValueError) as exc:
        raise RestoreValidationError("cannot reconstruct restored automation identities") from exc

    return _CurrentIndex(
        task_ids=frozenset(task_ids),
        run_owner=run_owner,
        project_ids=frozenset(project.id for project in deployment.scopes.list_projects()),
        workspace_projects=_workspace_projects(deployment),
        file_ids=_file_ids(deployment),
        artifact_ids=frozenset(artifact_ids),
        result_ids=frozenset(result_ids),
        user_ids=frozenset(deployment.authentication.store.users),
        automation_ids=frozenset(item.id for item in automations),
        verification_ids=frozenset(
            item.verification_id for item in deployment.verification.snapshot_requests()
        ),
        event_ids=await _event_ids(deployment),
    )


def _workspace_projects(deployment: SingleNodeDeployment) -> dict[str, str]:
    workspaces = {
        workspace.id: workspace.project_id for workspace in deployment.scopes.list_workspaces()
    }
    database = deployment.config.database_dir / "workspaces.sqlite3"
    try:
        with sqlite3.connect(f"file:{database}?mode=ro&immutable=1", uri=True) as connection:
            rows = connection.execute(
                "SELECT workspace_id, project_id FROM workspace_metadata ORDER BY workspace_id"
            ).fetchall()
    except sqlite3.Error as exc:
        raise RestoreValidationError("cannot enumerate restored workspace identities") from exc
    for workspace_id, project_id in rows:
        current = workspaces.get(str(workspace_id))
        if current is not None and current != str(project_id):
            raise RestoreValidationError(
                f"workspace {workspace_id} has conflicting project identities"
            )
        workspaces[str(workspace_id)] = str(project_id)
    return workspaces


def _file_ids(deployment: SingleNodeDeployment) -> frozenset[str]:
    database = deployment.config.database_dir / "files.sqlite3"
    try:
        with sqlite3.connect(f"file:{database}?mode=ro&immutable=1", uri=True) as connection:
            rows = connection.execute("SELECT file_id FROM data_files ORDER BY file_id").fetchall()
    except sqlite3.Error as exc:
        raise RestoreValidationError("cannot enumerate restored file identities") from exc
    return frozenset(str(row[0]) for row in rows)


async def _event_ids(deployment: SingleNodeDeployment) -> frozenset[str]:
    event_ids: set[str] = set()
    try:
        for stream_id in await deployment.kernel_repository.list_stream_ids():
            for event in await deployment.kernel_repository.read_events(stream_id):
                event_ids.add(event.id)
    except ContractError as exc:
        raise RestoreValidationError("cannot enumerate restored canonical events") from exc
    return frozenset(event_ids)


def _validate_notifications(deployment: SingleNodeDeployment, index: _CurrentIndex) -> int:
    database = deployment.config.database_dir / "notifications.sqlite3"
    try:
        with sqlite3.connect(f"file:{database}?mode=ro&immutable=1", uri=True) as connection:
            notification_rows = connection.execute(
                "SELECT id, recipient_type, recipient_id, payload FROM notifications ORDER BY id"
            ).fetchall()
            preference_rows = connection.execute(
                "SELECT recipient_type, recipient_id, payload FROM notification_preferences "
                "ORDER BY recipient_type, recipient_id"
            ).fetchall()
            delivery_rows = connection.execute(
                "SELECT id, notification_id, payload FROM notification_delivery_attempts ORDER BY id"
            ).fetchall()
            cursor_rows = connection.execute(
                "SELECT event_id FROM notification_processed_events ORDER BY event_id"
            ).fetchall()
    except sqlite3.Error as exc:
        raise RestoreValidationError("cannot inspect restored notification state") from exc

    notification_ids = frozenset(str(row[0]) for row in notification_rows)
    for row_id, row_recipient_type, row_recipient_id, payload in notification_rows:
        entity = f"notification {row_id}"
        item = _json_document(str(payload), entity)
        if _required_string(item, "id") != str(row_id):
            raise RestoreValidationError(f"{entity} payload identity is inconsistent")
        recipient = _required_object(item, "recipient", entity)
        recipient_type = _required_string(recipient, "type")
        recipient_id = _required_string(recipient, "id")
        if recipient_type != str(row_recipient_type) or recipient_id != str(row_recipient_id):
            raise RestoreValidationError(f"{entity} recipient columns differ from payload")
        _validate_recipient(recipient_type, recipient_id, index=index, entity=entity)

        project_id = _optional_string(item, "project_id", entity)
        workspace_id = _optional_string(item, "workspace_id", entity)
        _validate_project_workspace_pair(project_id, workspace_id, index=index, entity=entity)

        task_id = _optional_string(item, "task_id", entity)
        if task_id is not None and task_id not in index.task_ids:
            raise RestoreValidationError(f"{entity} references missing task {task_id}")
        run_id = _optional_string(item, "run_id", entity)
        if run_id is not None:
            run_task = index.run_owner.get(run_id)
            if run_task is None:
                raise RestoreValidationError(f"{entity} references missing run {run_id}")
            if task_id is not None and run_task != task_id:
                raise RestoreValidationError(f"{entity} run does not belong to its task")

        automation_id = _optional_string(item, "automation_id", entity)
        if automation_id is not None and automation_id not in index.automation_ids:
            raise RestoreValidationError(
                f"{entity} references missing automation {automation_id}"
            )
        verification_id = _optional_string(item, "verification_id", entity)
        if verification_id is not None and verification_id not in index.verification_ids:
            raise RestoreValidationError(
                f"{entity} references missing verification {verification_id}"
            )

        source = _required_object(item, "source", entity)
        _validate_resource_reference(
            deployment,
            _required_string(source, "resource_type"),
            _required_string(source, "resource_id"),
            index=index,
            notification_ids=notification_ids,
            entity=f"{entity} source",
        )
        resource = item.get("resource_ref")
        if resource is not None:
            resource_ref = _object(resource, f"{entity} resource_ref")
            _validate_resource_reference(
                deployment,
                _required_string(resource_ref, "resource_type"),
                _required_string(resource_ref, "resource_id"),
                index=index,
                notification_ids=notification_ids,
                entity=f"{entity} resource",
            )
        for action in _array(item.get("actions", []), f"{entity} actions"):
            action_data = _object(action, f"{entity} action")
            resource_type = _optional_string(action_data, "resource_type", entity)
            resource_id = _optional_string(action_data, "resource_id", entity)
            if (resource_type is None) != (resource_id is None):
                raise RestoreValidationError(f"{entity} action resource reference is incomplete")
            if resource_type is not None and resource_id is not None:
                _validate_resource_reference(
                    deployment,
                    resource_type,
                    resource_id,
                    index=index,
                    notification_ids=notification_ids,
                    entity=f"{entity} action",
                )

    for row_recipient_type, row_recipient_id, payload in preference_rows:
        entity = f"notification preference {row_recipient_id}"
        item = _json_document(str(payload), entity)
        recipient = _required_object(item, "recipient", entity)
        recipient_type = _required_string(recipient, "type")
        recipient_id = _required_string(recipient, "id")
        if recipient_type != str(row_recipient_type) or recipient_id != str(row_recipient_id):
            raise RestoreValidationError(f"{entity} recipient columns differ from payload")
        _validate_recipient(recipient_type, recipient_id, index=index, entity=entity)
        projects = _string_array(item.get("project_ids", []), f"{entity} project_ids")
        missing_projects = set(projects) - set(index.project_ids)
        if missing_projects:
            raise RestoreValidationError(
                f"{entity} references missing projects {sorted(missing_projects)!r}"
            )

    for attempt_id, notification_id, payload in delivery_rows:
        entity = f"notification delivery {attempt_id}"
        notification = str(notification_id)
        if notification not in notification_ids:
            raise RestoreValidationError(
                f"{entity} references missing notification {notification}"
            )
        item = _json_document(str(payload), entity)
        if _required_string(item, "id") != str(attempt_id):
            raise RestoreValidationError(f"{entity} payload identity is inconsistent")
        if _required_string(item, "notification_id") != notification:
            raise RestoreValidationError(f"{entity} payload notification is inconsistent")
        _validate_recipient(
            _required_string(item, "recipient_type"),
            _required_string(item, "recipient_id"),
            index=index,
            entity=entity,
        )

    missing_events = {str(row[0]) for row in cursor_rows} - set(index.event_ids)
    if missing_events:
        raise RestoreValidationError(
            "notification event cursor references missing canonical events "
            f"{sorted(missing_events)!r}"
        )
    return len(notification_rows)


def _validate_templates(deployment: SingleNodeDeployment, index: _CurrentIndex) -> int:
    path = deployment.config.database_dir / "templates.json"
    if not path.is_file():
        return 0
    try:
        repository = JsonTemplateRepository(path)
        definitions = repository.list_templates()
    except (ContractError, OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise RestoreValidationError("cannot reconstruct restored template repository") from exc

    template_ids = frozenset(item.template_id for item in definitions)
    revision_refs = frozenset(
        (definition.template_id, revision.revision)
        for definition in definitions
        for revision in repository.list_revisions(definition.template_id)
    )

    checked = 0
    for definition in definitions:
        _validate_owner(
            deployment,
            definition.owner_ref.type,
            definition.owner_ref.id,
            index=index,
            entity=f"template {definition.template_id}",
        )
        _validate_project(definition.project_id, index, f"template {definition.template_id}")
        for revision in repository.list_revisions(definition.template_id):
            entity = f"template {definition.template_id}@{revision.revision}"
            _validate_owner(
                deployment,
                revision.owner_ref.type,
                revision.owner_ref.id,
                index=index,
                entity=entity,
            )
            _validate_project(revision.project_id, index, entity)
            for dependency in revision.content.dependencies:
                if dependency.template_id not in template_ids:
                    raise RestoreValidationError(
                        f"{entity} references missing template dependency {dependency.template_id}"
                    )
                if (
                    dependency.revision is not None
                    and (dependency.template_id, dependency.revision) not in revision_refs
                ):
                    raise RestoreValidationError(
                        f"{entity} references missing template revision "
                        f"{dependency.template_id}@{dependency.revision}"
                    )
            source_template = revision.content.provenance.source_template
            if source_template is not None and (
                source_template.template_id,
                source_template.revision,
            ) not in revision_refs:
                raise RestoreValidationError(
                    f"{entity} references missing provenance source "
                    f"{source_template.template_id}@{source_template.revision}"
                )
            checked += 1

    for instantiation in repository.list_instantiations():
        entity = f"template instance {instantiation.instance_id}"
        if (instantiation.source.template_id, instantiation.source.revision) not in revision_refs:
            raise RestoreValidationError(f"{entity} references missing source revision")
        _validate_owner(
            deployment,
            instantiation.applied_by.type,
            instantiation.applied_by.id,
            index=index,
            entity=entity,
        )
        for resource in instantiation.resource_refs:
            _validate_resource_reference(
                deployment,
                resource.resource_type,
                resource.resource_id,
                index=index,
                notification_ids=frozenset(),
                entity=entity,
            )
        checked += 1
    return checked


def _validate_project(project_id: str | None, index: _CurrentIndex, entity: str) -> None:
    if project_id is not None and project_id not in index.project_ids:
        raise RestoreValidationError(f"{entity} references missing project {project_id}")


def _validate_project_workspace_pair(
    project_id: str | None,
    workspace_id: str | None,
    *,
    index: _CurrentIndex,
    entity: str,
) -> None:
    _validate_project(project_id, index, entity)
    if workspace_id is None:
        return
    workspace_project = index.workspace_projects.get(workspace_id)
    if workspace_project is None:
        raise RestoreValidationError(f"{entity} references missing workspace {workspace_id}")
    if project_id is not None and workspace_project != project_id:
        raise RestoreValidationError(
            f"{entity} workspace {workspace_id} belongs to project {workspace_project}, "
            f"not {project_id}"
        )


def _validate_recipient(
    recipient_type: str,
    recipient_id: str,
    *,
    index: _CurrentIndex,
    entity: str,
) -> None:
    if recipient_type == "user" and recipient_id not in index.user_ids:
        raise RestoreValidationError(f"{entity} references missing user recipient {recipient_id}")
    # Team/organization recipient registries are not composed by the current single-node profile.


def _validate_owner(
    deployment: SingleNodeDeployment,
    owner_type: str,
    owner_id: str,
    *,
    index: _CurrentIndex,
    entity: str,
) -> None:
    if owner_type == "user" and owner_id not in index.user_ids:
        raise RestoreValidationError(f"{entity} references missing user owner {owner_id}")
    if owner_type == "automation" and owner_id not in index.automation_ids:
        raise RestoreValidationError(f"{entity} references missing automation owner {owner_id}")
    if owner_type == "agent":
        try:
            deployment.agents.repository.get_agent(owner_id)
        except ContractError as exc:
            raise RestoreValidationError(f"{entity} references missing agent owner {owner_id}") from exc
    if owner_type in {"agent_team", "agent-team"}:
        try:
            deployment.agents.repository.get_team(owner_id)
        except ContractError as exc:
            raise RestoreValidationError(
                f"{entity} references missing agent team owner {owner_id}"
            ) from exc


def _validate_resource_reference(
    deployment: SingleNodeDeployment,
    resource_type: str,
    resource_id: str,
    *,
    index: _CurrentIndex,
    notification_ids: frozenset[str],
    entity: str,
) -> None:
    if resource_type == "project" and resource_id not in index.project_ids:
        raise RestoreValidationError(f"{entity} references missing project {resource_id}")
    if resource_type == "workspace" and resource_id not in index.workspace_projects:
        raise RestoreValidationError(f"{entity} references missing workspace {resource_id}")
    if resource_type == "task" and resource_id not in index.task_ids:
        raise RestoreValidationError(f"{entity} references missing task {resource_id}")
    if resource_type == "run" and resource_id not in index.run_owner:
        raise RestoreValidationError(f"{entity} references missing run {resource_id}")
    if resource_type == "file" and resource_id not in index.file_ids:
        raise RestoreValidationError(f"{entity} references missing file {resource_id}")
    if resource_type == "artifact" and resource_id not in index.artifact_ids:
        raise RestoreValidationError(f"{entity} references missing artifact {resource_id}")
    if resource_type == "result" and resource_id not in index.result_ids:
        raise RestoreValidationError(f"{entity} references missing result {resource_id}")
    if resource_type == "automation" and resource_id not in index.automation_ids:
        raise RestoreValidationError(f"{entity} references missing automation {resource_id}")
    if resource_type == "verification" and resource_id not in index.verification_ids:
        raise RestoreValidationError(f"{entity} references missing verification {resource_id}")
    if resource_type == "notification" and resource_id not in notification_ids:
        raise RestoreValidationError(f"{entity} references missing notification {resource_id}")
    if resource_type == "agent":
        try:
            deployment.agents.repository.get_agent(resource_id)
        except ContractError as exc:
            raise RestoreValidationError(f"{entity} references missing agent {resource_id}") from exc
    if resource_type in {"agent_team", "agent-team"}:
        try:
            deployment.agents.repository.get_team(resource_id)
        except ContractError as exc:
            raise RestoreValidationError(
                f"{entity} references missing agent team {resource_id}"
            ) from exc
    # Other resource/source kinds may be owned by optional registries not composed here.


def _json_document(payload: str, entity: str) -> dict[str, object]:
    try:
        raw: object = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise RestoreValidationError(f"{entity} payload is invalid JSON") from exc
    return _object(raw, f"{entity} payload")


def _object(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise RestoreValidationError(f"{field} must be a JSON object")
    return cast(dict[str, object], value)


def _required_object(payload: dict[str, object], key: str, entity: str) -> dict[str, object]:
    if key not in payload:
        raise RestoreValidationError(f"{entity} is missing {key}")
    return _object(payload[key], f"{entity} {key}")


def _array(value: object, field: str) -> list[object]:
    if not isinstance(value, list):
        raise RestoreValidationError(f"{field} must be a JSON array")
    return cast(list[object], value)


def _string_array(value: object, field: str) -> list[str]:
    values = _array(value, field)
    if any(not isinstance(item, str) for item in values):
        raise RestoreValidationError(f"{field} must contain only strings")
    return [cast(str, item) for item in values]


def _required_string(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RestoreValidationError(f"{key} must be a non-blank string")
    return value


def _optional_string(payload: dict[str, object], key: str, entity: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise RestoreValidationError(f"{entity} {key} must be a non-blank string when provided")
    return value
