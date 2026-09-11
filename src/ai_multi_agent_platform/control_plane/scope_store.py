"""Canonical Project/Workspace identity storage for the Control Plane."""

from __future__ import annotations

import json
from collections.abc import Mapping

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import OwnerRef, Project, new_id, validate_id

from .models import OwnerType, WorkspaceIdentity


class ScopeStore:
    """Canonical Project/Workspace identity store with lossless Project snapshots."""

    def __init__(self) -> None:
        self._projects: dict[str, Project] = {}
        self._workspaces: dict[str, WorkspaceIdentity] = {}
        self._commands: dict[tuple[str, str], str] = {}

    def create_project(
        self,
        *,
        key: str,
        name: str,
        owner_type: OwnerType,
        owner_id: str,
        project_id: str | None = None,
    ) -> Project:
        existing = self._commands.get(("project.create", key))
        if existing is not None:
            return self.get_project(existing)
        canonical_id = project_id or new_id("project")
        validate_id(canonical_id, "project")
        if canonical_id in self._projects:
            raise ContractError(ErrorCode.CONFLICT, f"project already exists: {canonical_id}")
        project = Project(
            id=canonical_id,
            name=name,
            owner_ref=OwnerRef(type=owner_type, id=owner_id),
        )
        self._validate_project_snapshot(project)
        self._projects[canonical_id] = project
        self._commands[("project.create", key)] = canonical_id
        return project

    def store_project_snapshot(self, *, key: str, project: Project) -> Project:
        """Persist one complete canonical Project without reconstructing it field-by-field."""

        if not key.strip():
            raise ContractError(ErrorCode.INVALID_REQUEST, "project snapshot key must not be blank")
        self._validate_project_snapshot(project)
        command_key = ("project.snapshot.store", key)
        existing = self._commands.get(command_key)
        if existing is not None:
            stored = self.get_project(existing)
            if stored != project:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "project snapshot idempotency key is bound to a different Project",
                )
            return stored
        if project.id in self._projects:
            raise ContractError(ErrorCode.CONFLICT, f"project already exists: {project.id}")
        self._projects[project.id] = project
        self._commands[command_key] = project.id
        return project

    def compensate_project(
        self,
        project_id: str,
        *,
        external_dependencies: tuple[str, ...] | None = None,
    ) -> Project:
        """Remove only a proven-unreferenced Project created by a compensatable operation."""

        project = self._require_project_compensation_safe(
            project_id,
            external_dependencies=external_dependencies,
        )
        del self._projects[project_id]
        self._forget_project_commands(project_id)
        return project

    def _require_project_compensation_safe(
        self,
        project_id: str,
        *,
        external_dependencies: tuple[str, ...] | None,
    ) -> Project:
        project = self.get_project(project_id)
        workspace_dependencies = tuple(
            sorted(
                workspace.id
                for workspace in self._workspaces.values()
                if workspace.project_id == project_id
            )
        )
        if workspace_dependencies:
            raise ContractError(
                ErrorCode.CONFLICT,
                "project compensation refused because Workspace dependencies exist",
                details={"dependencies": list(workspace_dependencies)},
            )
        if external_dependencies is None:
            raise ContractError(
                ErrorCode.CONFLICT,
                "project compensation refused because cross-domain safety was not proven",
            )
        normalized = tuple(sorted(set(external_dependencies)))
        if normalized:
            raise ContractError(
                ErrorCode.CONFLICT,
                "project compensation refused because canonical dependencies exist",
                details={"dependencies": list(normalized)},
            )
        return project

    def _forget_project_commands(self, project_id: str) -> None:
        for command_key, result_id in tuple(self._commands.items()):
            if result_id == project_id and command_key[0] in {
                "project.create",
                "project.snapshot.store",
            }:
                del self._commands[command_key]

    @staticmethod
    def _validate_project_snapshot(project: Project) -> None:
        try:
            metadata = {
                "provenance": None
                if project.provenance is None
                else {
                    "source": project.provenance.source,
                    "actor_ref": project.provenance.actor_ref,
                    "details": _project_json_value(project.provenance.details),
                },
                "external_refs": [
                    {
                        "system": item.system,
                        "kind": item.kind,
                        "value": item.value,
                    }
                    for item in project.external_refs
                ],
            }
            json.dumps(
                metadata,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "Project provenance/external references must be JSON-safe",
            ) from exc

    def get_project(self, project_id: str) -> Project:
        validate_id(project_id, "project")
        try:
            return self._projects[project_id]
        except KeyError as exc:
            raise ContractError(ErrorCode.NOT_FOUND, f"project not found: {project_id}") from exc

    def list_projects(self) -> tuple[Project, ...]:
        return tuple(self._projects.values())

    def create_workspace(
        self,
        *,
        key: str,
        project_id: str,
        workspace_id: str | None = None,
    ) -> WorkspaceIdentity:
        existing = self._commands.get(("workspace.create", key))
        if existing is not None:
            return self.get_workspace(existing)
        project = self.get_project(project_id)
        workspace = WorkspaceIdentity(
            id=workspace_id or "",
            project_id=project_id,
            owner_type=project.owner_ref.type,
            owner_id=project.owner_ref.id,
        )
        if workspace.id in self._workspaces:
            raise ContractError(ErrorCode.CONFLICT, f"workspace already exists: {workspace.id}")
        self._workspaces[workspace.id] = workspace
        self._commands[("workspace.create", key)] = workspace.id
        return workspace

    def get_workspace(self, workspace_id: str) -> WorkspaceIdentity:
        validate_id(workspace_id, "workspace")
        try:
            return self._workspaces[workspace_id]
        except KeyError as exc:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"workspace not found: {workspace_id}",
            ) from exc

    def list_workspaces(self) -> tuple[WorkspaceIdentity, ...]:
        return tuple(self._workspaces.values())


def _project_json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, str | int | bool):
        return value
    if isinstance(value, float):
        if value != value or value in {float("inf"), float("-inf")}:
            raise ValueError("Project metadata contains a non-finite number")
        return value
    if isinstance(value, Mapping):
        converted: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("Project metadata keys must be strings")
            converted[key] = _project_json_value(item)
        return converted
    if isinstance(value, list | tuple):
        return [_project_json_value(item) for item in value]
    raise TypeError(f"Project metadata contains unsupported value: {type(value).__name__}")
