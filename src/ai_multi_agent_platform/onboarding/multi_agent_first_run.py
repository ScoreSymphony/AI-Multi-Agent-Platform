"""Official first-run multi-agent product workflow for issue #894.

This module deliberately composes existing canonical platform domains. It does not own a second
planner, executor, lifecycle store, or verification namespace. The production deployment injects
the #889 reference multi-agent planner and the ordinary Task/Plan/Step/Run kernel.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Protocol
from uuid import NAMESPACE_URL, uuid5

from ai_multi_agent_platform.agents import (
    AgentInstructions,
    AgentProfile,
    AgentRevisionRef,
    InstructionSource,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane.models import RequestContext
from ai_multi_agent_platform.data import DataAccessContext, FileProvider
from ai_multi_agent_platform.domain import OwnerRef, TaskStatus
from ai_multi_agent_platform.planning import PlanningService, ProposalStatus
from ai_multi_agent_platform.security import (
    ActorIdentity,
    ActorType,
    AuthorizationAction,
    LocalPrincipalPolicy,
    ResourceType,
)

from .first_run_service import FIRST_RUN_RESOURCE_ID, OnboardingService

ONBOARDING_RUN_MULTI_AGENT_GOLDEN_PATH_COMMAND = "onboarding.run-multi-agent-golden-path"

# Shared wire-level selector consumed by the #889 planner. Keep this product extension independent
# from the deployment package so onboarding remains importable without a deployment-layer cycle.
_REFERENCE_MULTI_AGENT_CONSTRAINT = "runtime:reference-multi-agent"

_FIRST_RUN_ROLES = (
    ("researcher", "First-run Researcher"),
    ("developer", "First-run Developer"),
    ("reviewer", "First-run Reviewer"),
)


class GoldenPathKernel(Protocol):
    async def create_task(self, **kwargs: object) -> Any: ...
    async def ready_task(self, **kwargs: object) -> Any: ...
    async def get_task(self, task_id: str) -> Any: ...
    async def get_run(self, task_id: str, run_id: str) -> Any: ...
    async def attach_artifact(self, **kwargs: object) -> Any: ...


class GoldenPathScopes(Protocol):
    def list_projects(self) -> Any: ...
    def get_project(self, project_id: str) -> Any: ...
    def list_workspaces(self, *, project_id: str | None = None) -> Any: ...
    def get_workspace(self, workspace_id: str) -> Any: ...


class GoldenPathAgents(Protocol):
    @property
    def repository(self) -> Any: ...

    def create_agent(self, profile: AgentProfile, **kwargs: object) -> Any: ...


class GoldenPathAuthorization(Protocol):
    def has_policy(self, principal_ref: str) -> bool: ...
    def register(self, policy: LocalPrincipalPolicy) -> None: ...


class GoldenPathCoordination(Protocol):
    def get_plan(self, plan_id: str) -> Any: ...
    def list_step_records(self, plan_id: str) -> Any: ...


class GoldenPathVerification(Protocol):
    def history(self, *, task_id: str) -> Any: ...


class MultiAgentFirstRunService:
    """Run the canonical #889 multi-agent path as the official product first-run workflow."""

    def __init__(
        self,
        *,
        onboarding: OnboardingService,
        kernel: GoldenPathKernel,
        planning: PlanningService,
        scopes: GoldenPathScopes,
        agents: GoldenPathAgents,
        authorization: GoldenPathAuthorization,
        coordination: GoldenPathCoordination,
        files: FileProvider,
        verification: GoldenPathVerification,
    ) -> None:
        self._onboarding = onboarding
        self._kernel = kernel
        self._planning = planning
        self._scopes = scopes
        self._agents = agents
        self._authorization = authorization
        self._coordination = coordination
        self._files = files
        self._verification = verification

    async def run(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        if resource_ref != FIRST_RUN_RESOURCE_ID:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                f"multi-agent first-run requires resource_ref={FIRST_RUN_RESOURCE_ID!r}",
            )
        owner = self._owner(context)
        objective = self._required_string(payload, "objective")
        title = self._optional_string(payload, "title") or "First multi-agent goal"
        key = context.idempotency_key or context.request_id

        status = self._onboarding.status(context)
        usable_models = status.get("usable_golden_path_model_count")
        if not isinstance(usable_models, int) or usable_models < 1:
            installed_adapters = status.get("installed_model_adapter_ids")
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "No usable local/self-hosted text model is configured for the multi-agent first run. "
                "Configure or repair one in Onboarding, then retry the same goal.",
                details={
                    "action": "onboarding.configure-model",
                    "installed_model_adapter_ids": (
                        list(installed_adapters)
                        if isinstance(installed_adapters, (list, tuple))
                        else []
                    ),
                },
            )

        project_id, workspace_id = self._scope(
            owner,
            project_id=self._optional_string(payload, "project_id"),
            workspace_id=self._optional_string(payload, "workspace_id"),
        )
        agents = self._ensure_agents(owner, project_id=project_id, workspace_id=workspace_id)

        task = await self._kernel.create_task(
            idempotency_key=f"{key}:create-task",
            title=title,
            objective=objective,
            owner_type=owner.type,
            owner_id=owner.id,
            project_id=project_id,
            actor_ref=context.actor.principal_ref,
            source="onboarding.multi-agent-first-run",
        )
        if task.status is TaskStatus.DRAFT:
            task = await self._kernel.ready_task(
                idempotency_key=f"{key}:ready-task",
                task_id=task.task_id,
                actor_ref=context.actor.principal_ref,
                source="onboarding.multi-agent-first-run",
            )

        proposal = await self._planning.propose(
            task_id=task.task_id,
            idempotency_key=f"{key}:propose",
            workspace_id=workspace_id,
            task_constraints=(_REFERENCE_MULTI_AGENT_CONSTRAINT,),
        )
        if proposal.status is not ProposalStatus.VALIDATED:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "The built-in multi-agent plan could not be validated. Inspect the proposal "
                "validation details, repair the local model/Agent configuration, and retry.",
                details={
                    "proposal_id": proposal.proposal.proposal_id,
                    "validation_errors": list(proposal.validation.errors),
                    "validation_warnings": list(proposal.validation.warnings),
                },
            )
        activated = await self._planning.activate(
            proposal.proposal.proposal_id,
            idempotency_key=f"{key}:activate",
            actor=ActorIdentity(owner.id, ActorType.HUMAN),
        )
        plan_id = activated.activation_plan_id
        if plan_id is None:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "Validated first-run proposal did not expose an activated canonical Plan.",
            )

        return await self._project(
            task_id=task.task_id,
            plan_id=plan_id,
            project_id=project_id,
            workspace_id=workspace_id,
            owner=owner,
            actor_ref=context.actor.principal_ref,
            key=key,
            agent_refs=agents,
        )

    def _owner(self, context: RequestContext) -> OwnerRef:
        owner_type = context.actor.owner_type
        owner_id = context.actor.owner_id
        if owner_type is None or owner_id is None:
            raise ContractError(
                ErrorCode.UNAUTHORIZED,
                "The multi-agent first run requires an authenticated owner context.",
            )
        return OwnerRef(type=owner_type, id=owner_id)

    def _scope(
        self,
        owner: OwnerRef,
        *,
        project_id: str | None,
        workspace_id: str | None,
    ) -> tuple[str, str]:
        owned_projects = tuple(
            project
            for project in self._scopes.list_projects()
            if project.owner_type == owner.type and project.owner_id == owner.id
        )
        if project_id is None:
            if len(owned_projects) != 1:
                raise ContractError(
                    ErrorCode.INVALID_REQUEST,
                    "Select a Project for the first multi-agent goal.",
                    details={"candidate_project_ids": [item.id for item in owned_projects]},
                )
            project_id = owned_projects[0].id
        project = self._scopes.get_project(project_id)
        if project.owner_type != owner.type or project.owner_id != owner.id:
            raise ContractError(ErrorCode.FORBIDDEN, "Selected Project is not owned by this actor.")

        owned_workspaces = tuple(
            workspace
            for workspace in self._scopes.list_workspaces(project_id=project_id)
            if workspace.owner_type == owner.type and workspace.owner_id == owner.id
        )
        if workspace_id is None:
            if len(owned_workspaces) != 1:
                raise ContractError(
                    ErrorCode.INVALID_REQUEST,
                    "Select a Workspace for the first multi-agent goal.",
                    details={"candidate_workspace_ids": [item.id for item in owned_workspaces]},
                )
            workspace_id = owned_workspaces[0].id
        workspace = self._scopes.get_workspace(workspace_id)
        if workspace.project_id != project_id:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "Selected Workspace does not belong to the selected Project.",
            )
        if workspace.owner_type != owner.type or workspace.owner_id != owner.id:
            raise ContractError(ErrorCode.FORBIDDEN, "Selected Workspace is not owned by this actor.")
        return project_id, workspace_id

    def _ensure_agents(
        self,
        owner: OwnerRef,
        *,
        project_id: str,
        workspace_id: str,
    ) -> dict[str, AgentRevisionRef]:
        existing = {
            definition.agent_id: definition for definition in self._agents.repository.list_agents()
        }
        refs: dict[str, AgentRevisionRef] = {}
        for role, name in _FIRST_RUN_ROLES:
            agent_id = self._stable_id("agent", owner.type, owner.id, project_id, workspace_id, role)
            definition = existing.get(agent_id)
            if definition is None:
                revision = self._agents.create_agent(
                    AgentProfile(
                        name=name,
                        role=role,
                        description=f"Built-in {role} for the official first-run multi-agent workflow.",
                        instructions=AgentInstructions(
                            role=InstructionSource(
                                content=(
                                    f"Act as the {role} in the platform's built-in first-run team. "
                                    "Use canonical context and only capabilities that are actually granted."
                                ),
                                version="1",
                            )
                        ),
                        metadata={
                            "official_first_run": True,
                            "official_first_run_role": role,
                        },
                    ),
                    owner_ref=owner,
                    project_id=project_id,
                    workspace_id=workspace_id,
                    agent_id=agent_id,
                )
            else:
                if (
                    definition.owner_ref != owner
                    or definition.project_id != project_id
                    or definition.workspace_id != workspace_id
                ):
                    raise ContractError(
                        ErrorCode.CONFLICT,
                        "Stable first-run Agent identity is occupied outside the selected scope.",
                        details={"agent_id": agent_id, "role": role},
                    )
                revision = self._agents.repository.get_agent_revision(
                    agent_id, definition.current_revision
                )
                if revision.profile.role != role or not revision.profile.metadata.get(
                    "official_first_run"
                ):
                    raise ContractError(
                        ErrorCode.CONFLICT,
                        "Stable first-run Agent identity is occupied by a different definition.",
                        details={"agent_id": agent_id, "role": role},
                    )
            ref = AgentRevisionRef(revision.agent_id, revision.revision)
            refs[role] = ref
            principal_ref = f"agent:{ref.agent_id}@{ref.revision}"
            if not self._authorization.has_policy(principal_ref):
                self._authorization.register(
                    LocalPrincipalPolicy(
                        principal_ref=principal_ref,
                        actor_types=frozenset({ActorType.AGENT}),
                        allowed_actions=frozenset(
                            {AuthorizationAction.READ, AuthorizationAction.RESULT_READ}
                        ),
                        resource_types=frozenset(
                            {ResourceType.ARTIFACT, ResourceType.GENERIC}
                        ),
                    )
                )
        return refs

    async def _project(
        self,
        *,
        task_id: str,
        plan_id: str,
        project_id: str,
        workspace_id: str,
        owner: OwnerRef,
        actor_ref: str,
        key: str,
        agent_refs: Mapping[str, AgentRevisionRef],
    ) -> dict[str, JsonValue]:
        state = self._coordination.get_plan(plan_id)
        records = {item.step_id: item for item in self._coordination.list_step_records(plan_id)}
        steps: list[JsonValue] = []
        result_ids: list[str] = []
        artifact_ids: list[str] = []
        final_run_id: str | None = None
        for step in state.steps:
            record = records[step.id]
            run_result_ids: list[str] = []
            run_artifact_ids: list[str] = []
            if record.latest_run_id is not None:
                run = await self._kernel.get_run(task_id, record.latest_run_id)
                run_result_ids = list(run.result_ids)
                run_artifact_ids = list(run.artifact_ids)
                result_ids.extend(run_result_ids)
                artifact_ids.extend(run_artifact_ids)
                final_run_id = record.latest_run_id
            assignment = step.assignment
            steps.append(
                {
                    "step_id": step.id,
                    "title": step.title,
                    "status": step.status.value,
                    "phase": record.phase.value,
                    "dependency_ids": list(record.dependency_ids),
                    "satisfied_dependency_ids": list(record.satisfied_dependency_ids),
                    "agent_id": assignment.agent_id if assignment is not None else None,
                    "agent_revision": assignment.agent_revision if assignment is not None else None,
                    "run_id": record.latest_run_id,
                    "result_ids": run_result_ids,
                    "artifact_ids": run_artifact_ids,
                }
            )

        if final_run_id is not None:
            artifact_id = self._stable_id("artifact", task_id, "first-run-summary")
            file_id = self._stable_id("file", task_id, "first-run-summary")
            data_context = DataAccessContext(
                operation=OperationContext(
                    correlation_id=task_id,
                    causation_id=key,
                    owner_type=owner.type,
                    owner_id=owner.id,
                    project_id=project_id,
                ),
                actor_ref=actor_ref,
                task_id=task_id,
                run_id=final_run_id,
            )
            summary = {
                "task_id": task_id,
                "plan_id": plan_id,
                "workspace_id": workspace_id,
                "steps": steps,
                "result_ids": result_ids,
            }
            try:
                await self._files.create_file(
                    json.dumps(summary, indent=2, sort_keys=True).encode("utf-8"),
                    data_context,
                    file_id=file_id,
                    content_type="application/json",
                    metadata={
                        "purpose": "official-first-run-multi-agent-summary",
                        "task_id": task_id,
                        "plan_id": plan_id,
                    },
                )
            except ContractError as exc:
                if exc.code is not ErrorCode.CONFLICT:
                    raise
                await self._files.get_file(file_id, data_context)
            await self._files.link_artifact(file_id, artifact_id, data_context)
            await self._kernel.attach_artifact(
                idempotency_key=f"{key}:attach-summary-artifact",
                task_id=task_id,
                run_id=final_run_id,
                artifact_id=artifact_id,
                actor_ref=actor_ref,
                source="onboarding.multi-agent-first-run",
            )
            if artifact_id not in artifact_ids:
                artifact_ids.append(artifact_id)

        task = await self._kernel.get_task(task_id)
        verification_history = self._verification.history(task_id=task_id)
        review_step = next(
            (item for item in steps if isinstance(item, dict) and "Review" in str(item.get("title"))),
            None,
        )
        verification_items: list[JsonValue] = []
        for request, result in verification_history:
            verification_items.append(
                {
                    "verification_id": request.verification_id,
                    "stage_id": request.stage_id,
                    "subject_type": request.subject.subject_type,
                    "subject_id": request.subject.subject_id,
                    "outcome": result.outcome.value if result is not None else None,
                }
            )

        return {
            "id": task_id,
            "type": "multi_agent_first_run_result",
            "task_id": task_id,
            "task_status": task.status.value,
            "plan_id": plan_id,
            "project_id": project_id,
            "workspace_id": workspace_id,
            "agents": {
                role: {"agent_id": ref.agent_id, "revision": ref.revision}
                for role, ref in agent_refs.items()
            },
            "steps": steps,
            "result_ids": list(dict.fromkeys(result_ids)),
            "artifact_ids": list(dict.fromkeys(artifact_ids)),
            "review": {
                "step": review_step,
                "status": (
                    "passed"
                    if isinstance(review_step, dict) and review_step.get("status") == "succeeded"
                    else "incomplete"
                ),
            },
            "verification": {
                "canonical_records": verification_items,
                "reviewer_step_is_baseline_check": True,
            },
            "trace": {
                "task_id": task_id,
                "plan_id": plan_id,
                "step_ids": [step.id for step in state.steps],
            },
        }

    @staticmethod
    def _stable_id(prefix: str, *parts: str) -> str:
        return f"{prefix}_{uuid5(NAMESPACE_URL, ':'.join(parts))}"

    @staticmethod
    def _required_string(payload: Mapping[str, object], key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ContractError(ErrorCode.INVALID_REQUEST, f"{key} must be a non-blank string")
        return value.strip()

    @staticmethod
    def _optional_string(payload: Mapping[str, object], key: str) -> str | None:
        value = payload.get(key)
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ContractError(ErrorCode.INVALID_REQUEST, f"{key} must be a non-blank string")
        return value.strip()


__all__ = [
    "MultiAgentFirstRunService",
    "ONBOARDING_RUN_MULTI_AGENT_GOLDEN_PATH_COMMAND",
]
