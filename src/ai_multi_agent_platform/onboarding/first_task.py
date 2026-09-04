"""First-run Task orchestration over existing canonical Project, Agent, Model and Kernel APIs."""

from __future__ import annotations

from ai_multi_agent_platform.agents import STARTER_CATALOG_SOURCE, AgentService
from ai_multi_agent_platform.contracts import ContractError, ErrorCode, JsonValue
from ai_multi_agent_platform.control_plane.models import RequestContext, WorkspaceIdentity
from ai_multi_agent_platform.control_plane.service import ScopeStore
from ai_multi_agent_platform.domain import Project, RunStatus, TaskStatus
from ai_multi_agent_platform.kernel import PlatformKernel

from .agent_lifecycle import (
    FIRST_RUN_AGENT_EXECUTION_PROFILE,
    FIRST_RUN_AGENT_ID_KEY,
    FIRST_RUN_EXECUTION_PROFILE_KEY,
    FIRST_RUN_WORKSPACE_ID_KEY,
)
from .service import FIRST_RUN_RESOURCE_ID, OnboardingService

ONBOARDING_RUN_FIRST_TASK_COMMAND = "onboarding.run-first-task"


class FirstRunTaskService:
    """Drive one real General-Assistant Task without introducing replacement resources."""

    def __init__(
        self,
        *,
        onboarding: OnboardingService,
        kernel: PlatformKernel,
        scopes: ScopeStore,
        agents: AgentService,
    ) -> None:
        self.onboarding = onboarding
        self.kernel = kernel
        self.scopes = scopes
        self.agents = agents

    async def run_first_task(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        if resource_ref != FIRST_RUN_RESOURCE_ID:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                f"first-run Task requires resource_ref={FIRST_RUN_RESOURCE_ID!r}",
            )
        key = context.idempotency_key
        if key is None:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "onboarding.run-first-task requires an idempotency key",
            )
        owner_type = context.actor.owner_type
        owner_id = context.actor.owner_id
        if owner_type is None or owner_id is None:
            raise ContractError(
                ErrorCode.UNAUTHORIZED,
                "first-run Task requires an authenticated canonical owner",
            )
        status = self.onboarding.status(context)
        if status.get("state") != "ready_for_task":
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "first-run prerequisites are incomplete",
                details={
                    "state": str(status.get("state", "unknown")),
                    "guidance": status.get("guidance", []),
                },
            )

        objective = _required_string(payload, "objective")
        project = self._project(
            owner_type,
            owner_id,
            _optional_string(payload, "project_id"),
        )
        workspace = self._workspace(
            owner_type,
            owner_id,
            project.id,
            _optional_string(payload, "workspace_id"),
        )
        agent_id = self._general_assistant(
            owner_type,
            owner_id,
            project.id,
            workspace.id,
            _optional_string(payload, "agent_id"),
        )

        task = await self.kernel.create_task(
            idempotency_key=f"{key}:create-task",
            title=_optional_string(payload, "title") or "First General Assistant Task",
            objective=objective,
            owner_type=owner_type,
            owner_id=owner_id,
            project_id=project.id,
            actor_ref=context.actor.principal_ref,
            source="onboarding",
        )
        task = await self.kernel.update_task(
            idempotency_key=f"{key}:execution-profile",
            task_id=task.task_id,
            metadata={
                FIRST_RUN_EXECUTION_PROFILE_KEY: FIRST_RUN_AGENT_EXECUTION_PROFILE,
                FIRST_RUN_AGENT_ID_KEY: agent_id,
                FIRST_RUN_WORKSPACE_ID_KEY: workspace.id,
            },
            actor_ref=context.actor.principal_ref,
            source="onboarding",
        )
        if task.status is TaskStatus.DRAFT:
            await self.kernel.ready_task(
                idempotency_key=f"{key}:ready-task",
                task_id=task.task_id,
                actor_ref=context.actor.principal_ref,
                source="onboarding",
            )
        run = await self.kernel.start_task(
            idempotency_key=f"{key}:start-task",
            task_id=task.task_id,
            actor_ref=context.actor.principal_ref,
            source="onboarding",
        )
        run = await self.kernel.refresh_run(
            idempotency_key=f"{key}:refresh-run",
            task_id=task.task_id,
            run_id=run.run_id,
            actor_ref=context.actor.principal_ref,
            source="onboarding",
        )
        if run.status is not RunStatus.SUCCEEDED:
            message = run.output.get("error")
            raise ContractError(
                ErrorCode.PERMANENT_FAILURE,
                message if isinstance(message, str) else "first General Assistant Task failed",
                details={"task_id": task.task_id, "run_id": run.run_id},
            )
        result_id = run.output.get("result_id")
        if not isinstance(result_id, str):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "successful first-run Agent execution did not produce a canonical Result ID",
            )
        await self.kernel.attach_result(
            idempotency_key=f"{key}:attach-result",
            task_id=task.task_id,
            run_id=run.run_id,
            result_id=result_id,
            actor_ref=context.actor.principal_ref,
            source="onboarding",
        )
        persisted_task = await self.kernel.get_task(task.task_id)
        persisted_run = await self.kernel.get_run(task.task_id, run.run_id)
        return {
            "id": result_id,
            "type": "first_run_result",
            "task_id": persisted_task.task_id,
            "task_status": persisted_task.status.value,
            "run_id": persisted_run.run_id,
            "run_status": persisted_run.status.value,
            "agent_id": agent_id,
            "workspace_id": workspace.id,
            "project_id": project.id,
            "result_id": result_id,
            "output": dict(persisted_run.output),
        }

    def _project(
        self,
        owner_type: str,
        owner_id: str,
        project_id: str | None,
    ) -> Project:
        owned = tuple(
            project
            for project in self.scopes.list_projects()
            if project.owner_ref.type == owner_type and project.owner_ref.id == owner_id
        )
        if project_id is not None:
            project = self.scopes.get_project(project_id)
            if project not in owned:
                raise ContractError(ErrorCode.FORBIDDEN, "Project is not owned by the caller")
            return project
        if len(owned) != 1:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "project_id is required when first-run onboarding has more than one Project",
            )
        return owned[0]

    def _workspace(
        self,
        owner_type: str,
        owner_id: str,
        project_id: str,
        workspace_id: str | None,
    ) -> WorkspaceIdentity:
        owned = tuple(
            workspace
            for workspace in self.scopes.list_workspaces()
            if workspace.owner_type == owner_type
            and workspace.owner_id == owner_id
            and workspace.project_id == project_id
        )
        if workspace_id is not None:
            workspace = self.scopes.get_workspace(workspace_id)
            if workspace not in owned:
                raise ContractError(
                    ErrorCode.FORBIDDEN,
                    "Workspace is not owned by the caller or does not belong to the selected "
                    "Project",
                )
            return workspace
        if len(owned) != 1:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "workspace_id is required when the selected Project has more than one Workspace",
            )
        return owned[0]

    def _general_assistant(
        self,
        owner_type: str,
        owner_id: str,
        project_id: str,
        workspace_id: str,
        agent_id: str | None,
    ) -> str:
        candidates: list[str] = []
        for definition in self.agents.repository.list_agents():
            revision = self.agents.get_agent_revision(definition.agent_id)
            if (
                revision.owner_ref.type == owner_type
                and revision.owner_ref.id == owner_id
                and revision.project_id == project_id
                and revision.workspace_id == workspace_id
                and revision.profile.metadata.get("starter_key") == "general_assistant"
                and revision.profile.metadata.get("starter_catalog_source")
                == STARTER_CATALOG_SOURCE
                and revision.profile.enabled
            ):
                candidates.append(revision.agent_id)
        if agent_id is not None:
            if agent_id not in candidates:
                raise ContractError(
                    ErrorCode.FORBIDDEN,
                    "selected Agent is not an enabled owned General Assistant for this Workspace",
                )
            return agent_id
        if len(candidates) != 1:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "agent_id is required when the Workspace has more than one General Assistant",
            )
        return candidates[0]


def _required_string(payload: dict[str, JsonValue], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{key} must be a non-blank string")
    return value


def _optional_string(payload: dict[str, JsonValue], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{key} must be a non-blank string")
    return value
