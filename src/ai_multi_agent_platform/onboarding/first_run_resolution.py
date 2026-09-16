"""Deterministic first-run path selection and error classification."""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue

from .first_run_types import FirstRunPath, FirstRunPathProjection

FirstRunPreflight = Callable[[FirstRunPath], None]


def resolve_first_run_path(
    projection: FirstRunPathProjection,
    *,
    project_id: str | None,
    workspace_id: str | None,
    agent_id: str | None,
    preflight: FirstRunPreflight,
) -> FirstRunPath:
    """Resolve one executable path from an already owner-scoped projection."""

    _validate_explicit_selection(
        projection,
        project_id=project_id,
        workspace_id=workspace_id,
        agent_id=agent_id,
    )
    executable = filter_first_run_paths(
        projection.executable_paths,
        project_id=project_id,
        workspace_id=workspace_id,
        agent_id=agent_id,
    )
    if len(executable) == 1:
        return executable[0]
    if len(executable) > 1:
        _raise_selection_required(executable)

    structural = filter_first_run_paths(
        projection.structural_paths,
        project_id=project_id,
        workspace_id=workspace_id,
        agent_id=agent_id,
    )
    if len(structural) == 1:
        preflight(structural[0])
        raise AssertionError("first-run preflight unexpectedly accepted a blocked path")
    _raise_missing_path(
        projection,
        structural=structural,
        project_id=project_id,
        workspace_id=workspace_id,
    )
    raise AssertionError("first-run path error classification unexpectedly returned")


def filter_first_run_paths(
    paths: tuple[FirstRunPath, ...],
    *,
    project_id: str | None,
    workspace_id: str | None,
    agent_id: str | None,
) -> tuple[FirstRunPath, ...]:
    """Filter canonical paths by explicitly selected identity dimensions."""

    return tuple(
        path
        for path in paths
        if (project_id is None or path.project_id == project_id)
        and (workspace_id is None or path.workspace_id == workspace_id)
        and (agent_id is None or path.agent_id == agent_id)
    )


def first_run_selection_kind(paths: tuple[FirstRunPath, ...]) -> str | None:
    """Return the first canonical identity dimension that remains ambiguous."""

    if len({path.project_id for path in paths}) > 1:
        return "project"
    if len({path.workspace_id for path in paths}) > 1:
        return "workspace"
    if len({path.agent_id for path in paths}) > 1:
        return "agent"
    return None


def _validate_explicit_selection(
    projection: FirstRunPathProjection,
    *,
    project_id: str | None,
    workspace_id: str | None,
    agent_id: str | None,
) -> None:
    if project_id is not None and project_id not in projection.project_ids:
        raise ContractError(ErrorCode.FORBIDDEN, "Project is not owned by the caller")
    if workspace_id is not None:
        matching_workspaces = {
            candidate_workspace_id
            for candidate_project_id, candidate_workspace_id in projection.workspace_bindings
            if project_id is None or candidate_project_id == project_id
        }
        if workspace_id not in matching_workspaces:
            raise ContractError(
                ErrorCode.FORBIDDEN,
                "Workspace is not owned by the caller or does not belong to the selected Project",
            )
    if agent_id is None:
        return
    structural_agent_paths = filter_first_run_paths(
        projection.structural_paths,
        project_id=project_id,
        workspace_id=workspace_id,
        agent_id=agent_id,
    )
    if not structural_agent_paths:
        raise ContractError(
            ErrorCode.FORBIDDEN,
            "selected Agent is not an enabled owned General Assistant for the selected "
            "Project/Workspace",
        )


def _raise_selection_required(executable: tuple[FirstRunPath, ...]) -> None:
    selection_kind = first_run_selection_kind(executable)
    assert selection_kind is not None
    raise ContractError(
        ErrorCode.INVALID_REQUEST,
        f"{selection_kind}_id is required because multiple executable first-run paths remain",
        details={
            "selection_kind": selection_kind,
            "candidate_project_ids": cast(
                JsonValue, sorted({path.project_id for path in executable})
            ),
            "candidate_workspace_ids": cast(
                JsonValue, sorted({path.workspace_id for path in executable})
            ),
            "candidate_agent_ids": cast(JsonValue, sorted({path.agent_id for path in executable})),
        },
    )


def _raise_missing_path(
    projection: FirstRunPathProjection,
    *,
    structural: tuple[FirstRunPath, ...],
    project_id: str | None,
    workspace_id: str | None,
) -> None:
    if not projection.project_ids:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "first-run onboarding requires an owned Project",
        )
    matching_projects = {project_id} if project_id is not None else set(projection.project_ids)
    matching_workspace_bindings = tuple(
        binding for binding in projection.workspace_bindings if binding[0] in matching_projects
    )
    if not matching_workspace_bindings:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "first-run onboarding requires a Workspace for the selected Project",
        )
    if workspace_id is not None and not structural:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "selected Workspace has no executable enabled owned General Assistant",
        )
    raise ContractError(
        ErrorCode.INVALID_REQUEST,
        "no executable first-run path matches the current selection; review General Assistant "
        "preflight blockers",
        details={"general_assistant_blockers": cast(JsonValue, list(projection.blockers))},
    )
