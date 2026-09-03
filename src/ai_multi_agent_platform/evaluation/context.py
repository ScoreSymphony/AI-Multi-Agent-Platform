"""Attempt-scoped execution context passed from isolation into evaluation executors."""

from __future__ import annotations

from dataclasses import dataclass

from ai_multi_agent_platform.domain import validate_id


@dataclass(frozen=True, slots=True)
class EvaluationExecutionContext:
    """Portable per-attempt execution context without exposing host filesystem paths."""

    attempt_id: str
    project_id: str | None = None
    owner_type: str | None = None
    owner_id: str | None = None
    workspace_id: str | None = None
    workspace_snapshot_id: str | None = None
    workspace_content_checksum: str | None = None
    workspace_materialization_id: str | None = None
    execution_workspace: str | None = None

    def __post_init__(self) -> None:
        if not self.attempt_id.strip():
            raise ValueError("evaluation execution attempt_id must not be blank")
        if self.project_id is not None:
            validate_id(self.project_id, "project")
        if (self.owner_type is None) != (self.owner_id is None):
            raise ValueError("evaluation execution owner_type/owner_id must be provided together")
        if self.owner_type is not None and not self.owner_type.strip():
            raise ValueError("evaluation execution owner_type must not be blank")
        if self.owner_id is not None and not self.owner_id.strip():
            raise ValueError("evaluation execution owner_id must not be blank")

        workspace_values = (
            self.workspace_id,
            self.workspace_snapshot_id,
            self.workspace_content_checksum,
        )
        if any(value is not None for value in workspace_values) and not all(
            value is not None for value in workspace_values
        ):
            raise ValueError(
                "workspace_id, workspace_snapshot_id and workspace_content_checksum "
                "must be provided together"
            )
        if self.workspace_id is not None:
            validate_id(self.workspace_id, "workspace")
            assert self.workspace_snapshot_id is not None
            validate_id(self.workspace_snapshot_id, "workspace_snapshot")
            assert self.workspace_content_checksum is not None
            if not self.workspace_content_checksum.strip():
                raise ValueError("workspace_content_checksum must not be blank")

        materialization_values = (
            self.workspace_materialization_id,
            self.execution_workspace,
        )
        if any(value is not None for value in materialization_values) and not all(
            value is not None for value in materialization_values
        ):
            raise ValueError(
                "workspace_materialization_id and execution_workspace must be provided together"
            )
        if self.workspace_materialization_id is not None:
            if self.workspace_id is None:
                raise ValueError("workspace materialization requires canonical workspace identity")
            validate_id(self.workspace_materialization_id, "materialization")
            assert self.execution_workspace is not None
            if not self.execution_workspace.strip():
                raise ValueError("execution_workspace must not be blank")

    @property
    def has_workspace(self) -> bool:
        return self.workspace_id is not None
