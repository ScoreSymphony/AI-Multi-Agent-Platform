"""Shared support for the official multi-agent first-run product workflow."""

from __future__ import annotations

import json
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from ai_multi_agent_platform.agents import (
    AgentInstructions,
    AgentProfile,
    AgentRevisionRef,
    AgentService,
    InstructionSource,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.control_plane.models import RequestContext
from ai_multi_agent_platform.control_plane.service import ScopeStore
from ai_multi_agent_platform.data import DataAccessContext, FileProvider
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.security import (
    ActorType,
    AuthorizationAction,
    LocalPrincipalPolicy,
    ResourceType,
)
from ai_multi_agent_platform.verification import (
    ReviewerIndependence,
    VerificationPolicy,
    VerificationScope,
    VerificationStage,
    VerifierKind,
)

FIRST_RUN_WORKFLOW = "reference-multi-agent"
REFERENCE_MULTI_AGENT_CONSTRAINT = "runtime:reference-multi-agent"
VERIFICATION_STAGE_ID = "official-first-run-result-review"

_ROLE_TEMPLATES = (
    ("researcher", "First-run Research Agent"),
    ("developer", "First-run Execution Agent"),
    ("reviewer", "First-run Review Agent"),
)


def stable_id(prefix: str, *parts: str) -> str:
    seed = ":".join(("ai-multi-agent-platform", "official-first-run", *parts))
    return f"{prefix}_{uuid5(NAMESPACE_URL, seed)}"


def require_owner(context: RequestContext) -> OwnerRef:
    owner_type = context.actor.owner_type
    owner_id = context.actor.owner_id
    if owner_type is None or owner_id is None:
        raise ContractError(
            ErrorCode.UNAUTHORIZED,
            "The official multi-agent first run requires an authenticated owner.",
        )
    return OwnerRef(type=owner_type, id=owner_id)


def resolve_scope(
    scopes: ScopeStore,
    owner: OwnerRef,
    *,
    project_id: str | None,
    workspace_id: str | None,
) -> tuple[str, str]:
    owned_projects = tuple(item for item in scopes.list_projects() if item.owner_ref == owner)
    selected_project_id = _select_project(owned_projects, project_id)
    project = scopes.get_project(selected_project_id)
    if project.owner_ref != owner:
        raise ContractError(ErrorCode.FORBIDDEN, "Selected Project is not owned by this actor.")

    owned_workspaces = tuple(
        item
        for item in scopes.list_workspaces()
        if item.project_id == selected_project_id
        and item.owner_type == owner.type
        and item.owner_id == owner.id
    )
    selected_workspace_id = _select_workspace(owned_workspaces, workspace_id)
    workspace = scopes.get_workspace(selected_workspace_id)
    if workspace.project_id != selected_project_id:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "Selected Workspace does not belong to the selected Project.",
        )
    if workspace.owner_type != owner.type or workspace.owner_id != owner.id:
        raise ContractError(ErrorCode.FORBIDDEN, "Selected Workspace is not owned by this actor.")
    return selected_project_id, selected_workspace_id


def _select_project(projects: tuple[Any, ...], requested: str | None) -> str:
    if requested is not None:
        return requested
    if len(projects) == 1:
        return projects[0].id
    raise ContractError(
        ErrorCode.INVALID_REQUEST,
        "Select an owned Project for the official multi-agent first run.",
        details={"candidate_project_ids": [item.id for item in projects]},
    )


def _select_workspace(workspaces: tuple[Any, ...], requested: str | None) -> str:
    if requested is not None:
        return requested
    if len(workspaces) == 1:
        return workspaces[0].id
    raise ContractError(
        ErrorCode.INVALID_REQUEST,
        "Select an owned Workspace for the official multi-agent first run.",
        details={"candidate_workspace_ids": [item.id for item in workspaces]},
    )


def ensure_reference_agents(
    agents: AgentService,
    authorization: Any,
    *,
    owner: OwnerRef,
    project_id: str,
    workspace_id: str,
) -> dict[str, AgentRevisionRef]:
    existing = {item.agent_id: item for item in agents.repository.list_agents()}
    refs: dict[str, AgentRevisionRef] = {}
    for role, name in _ROLE_TEMPLATES:
        agent_id = stable_id("agent", owner.type, owner.id, project_id, workspace_id, role)
        definition = existing.get(agent_id)
        revision = (
            _create_role_agent(
                agents,
                agent_id=agent_id,
                role=role,
                name=name,
                owner=owner,
                project_id=project_id,
                workspace_id=workspace_id,
            )
            if definition is None
            else _require_role_agent(
                agents,
                definition,
                role=role,
                owner=owner,
                project_id=project_id,
                workspace_id=workspace_id,
            )
        )
        ref = AgentRevisionRef(revision.agent_id, revision.revision)
        refs[role] = ref
        _ensure_agent_policy(authorization, ref, project_id)
    return refs


def _create_role_agent(
    agents: AgentService,
    *,
    agent_id: str,
    role: str,
    name: str,
    owner: OwnerRef,
    project_id: str,
    workspace_id: str,
) -> Any:
    profile = AgentProfile(
        name=name,
        role=role,
        description=f"Built-in {role} for the official multi-agent first-run workflow.",
        instructions=AgentInstructions(
            role=InstructionSource(
                content=(
                    f"Act as the {role} in the official multi-agent first-run workflow. "
                    "Use canonical task context and handoffs; do not invent hidden state."
                ),
                version="1",
            )
        ),
        metadata={"official_first_run": True, "official_first_run_role": role},
    )
    return agents.create_agent(
        profile,
        owner_ref=owner,
        project_id=project_id,
        workspace_id=workspace_id,
        agent_id=agent_id,
    )


def _require_role_agent(
    agents: AgentService,
    definition: Any,
    *,
    role: str,
    owner: OwnerRef,
    project_id: str,
    workspace_id: str,
) -> Any:
    if (
        definition.owner_ref != owner
        or definition.project_id != project_id
        or definition.workspace_id != workspace_id
    ):
        raise ContractError(
            ErrorCode.CONFLICT,
            "Stable first-run Agent identity is occupied outside the selected scope.",
            details={"agent_id": definition.agent_id, "role": role},
        )
    revision = agents.repository.get_agent_revision(
        definition.agent_id, definition.current_revision
    )
    if revision.profile.role != role or not revision.profile.metadata.get("official_first_run"):
        raise ContractError(
            ErrorCode.CONFLICT,
            "Stable first-run Agent identity is occupied by a different definition.",
            details={"agent_id": definition.agent_id, "role": role},
        )
    return revision


def _ensure_agent_policy(authorization: Any, ref: AgentRevisionRef, project_id: str) -> None:
    principal_ref = f"agent:{ref.agent_id}@{ref.revision}"
    if authorization.has_policy(principal_ref):
        return
    authorization.register(
        LocalPrincipalPolicy(
            principal_ref=principal_ref,
            actor_types=frozenset({ActorType.AGENT}),
            allowed_actions=frozenset({AuthorizationAction.READ, AuthorizationAction.RESULT_READ}),
            resource_types=frozenset({ResourceType.ARTIFACT, ResourceType.GENERIC}),
            project_ids=frozenset({project_id}),
        )
    )


async def ensure_goal_artifact(
    files: FileProvider,
    kernel: Any,
    context: RequestContext,
    *,
    task_id: str,
    project_id: str,
    workspace_id: str,
    objective: str,
    idempotency_key: str,
) -> str:
    artifact_id = stable_id("artifact", task_id, "goal")
    file_id = stable_id("file", task_id, "goal")
    data_context = DataAccessContext(
        operation=OperationContext(
            correlation_id=context.correlation_id,
            causation_id=idempotency_key,
            owner_type=context.actor.owner_type,
            owner_id=context.actor.owner_id,
            project_id=project_id,
        ),
        actor_ref=context.actor.principal_ref,
        task_id=task_id,
    )
    payload = json.dumps(
        {
            "workflow": FIRST_RUN_WORKFLOW,
            "task_id": task_id,
            "project_id": project_id,
            "workspace_id": workspace_id,
            "objective": objective,
        },
        indent=2,
        sort_keys=True,
    ).encode("utf-8")
    try:
        await files.create_file(
            payload,
            data_context,
            file_id=file_id,
            content_type="application/json",
            metadata={"purpose": "official-first-run-goal"},
        )
    except ContractError as exc:
        if exc.code is not ErrorCode.CONFLICT:
            raise
        await files.get_file(file_id, data_context)
    await files.link_artifact(file_id, artifact_id, data_context)
    await kernel.attach_artifact(
        idempotency_key=f"{idempotency_key}:attach-goal-artifact",
        task_id=task_id,
        artifact_id=artifact_id,
        actor_ref=context.actor.principal_ref,
        source="onboarding.multi-agent-first-run",
    )
    return artifact_id


def ensure_verification(
    verification: Any,
    verification_runtime: Any,
    *,
    task_id: str,
    reviewer: AgentRevisionRef,
) -> str:
    policy_id = stable_id("verification_policy", task_id, "exact-result-review")
    try:
        policy = verification.get_policy(policy_id, 1)
    except ContractError as exc:
        if exc.code is not ErrorCode.NOT_FOUND:
            raise
        policy = verification.register_policy(
            VerificationPolicy(
                policy_id=policy_id,
                name="Official first-run exact-result review",
                scope=VerificationScope(task_ids=(task_id,)),
                stages=(
                    VerificationStage(
                        stage_id=VERIFICATION_STAGE_ID,
                        verifier_kind=VerifierKind.AGENT,
                    ),
                ),
                independence=ReviewerIndependence(producer_agent_must_differ=True),
                metadata={
                    "automatic_reviewer": {
                        "enabled": True,
                        "subject_types": ["result"],
                        "stages": {
                            VERIFICATION_STAGE_ID: {
                                "agent_id": reviewer.agent_id,
                                "agent_revision": reviewer.revision,
                            }
                        },
                    }
                },
            )
        )
    verification_runtime.require_task(
        task_id=task_id,
        policy_id=policy.policy_id,
        policy_version=policy.version,
    )
    return policy.policy_id
