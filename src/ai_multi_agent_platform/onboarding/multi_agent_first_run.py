"""Official product first run over the existing reference multi-agent runtime."""

from __future__ import annotations

from typing import Any

from ai_multi_agent_platform.agents import AgentService
from ai_multi_agent_platform.contracts import ContractError, ErrorCode, JsonValue
from ai_multi_agent_platform.control_plane.models import RequestContext
from ai_multi_agent_platform.control_plane.service import ScopeStore
from ai_multi_agent_platform.data import FileProvider
from ai_multi_agent_platform.domain import TaskStatus
from ai_multi_agent_platform.planning import PlanningService, ProposalStatus
from ai_multi_agent_platform.security import ActorIdentity, ActorType

from .first_run_service import FIRST_RUN_RESOURCE_ID, OnboardingService
from .multi_agent_first_run_projection import project_first_run_result
from .multi_agent_first_run_support import (
    REFERENCE_MULTI_AGENT_CONSTRAINT,
    ensure_goal_artifact,
    ensure_reference_agents,
    ensure_verification,
    require_owner,
    resolve_scope,
)

ONBOARDING_RUN_MULTI_AGENT_GOLDEN_PATH_COMMAND = "onboarding.run-multi-agent-golden-path"


class MultiAgentFirstRunService:
    """Productize the #889 runtime without becoming another lifecycle authority."""

    def __init__(
        self,
        *,
        onboarding: OnboardingService,
        kernel: Any,
        planning: PlanningService,
        scopes: ScopeStore,
        agents: AgentService,
        authorization: Any,
        coordination: Any,
        files: FileProvider,
        verification: Any,
        verification_runtime: Any,
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
        self._verification_runtime = verification_runtime

    async def run(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        key = self._validate_request(context, resource_ref)
        objective = _required_string(payload, "objective")
        owner = require_owner(context)
        self._require_model(context)
        project_id, workspace_id = resolve_scope(
            self._scopes,
            owner,
            project_id=_optional_string(payload, "project_id"),
            workspace_id=_optional_string(payload, "workspace_id"),
        )
        agents = ensure_reference_agents(
            self._agents,
            self._authorization,
            owner=owner,
            project_id=project_id,
            workspace_id=workspace_id,
        )
        task = await self._kernel.create_task(
            idempotency_key=f"{key}:create-task",
            title=_optional_string(payload, "title") or "First multi-agent goal",
            objective=objective,
            owner_type=owner.type,
            owner_id=owner.id,
            project_id=project_id,
            actor_ref=context.actor.principal_ref,
            source="onboarding.multi-agent-first-run",
        )
        artifact_id = await ensure_goal_artifact(
            self._files,
            self._kernel,
            context,
            task_id=task.task_id,
            project_id=project_id,
            workspace_id=workspace_id,
            objective=objective,
            idempotency_key=key,
        )
        ensure_verification(
            self._verification,
            self._verification_runtime,
            task_id=task.task_id,
            reviewer=agents["reviewer"],
        )
        if task.status is TaskStatus.DRAFT:
            await self._kernel.ready_task(
                idempotency_key=f"{key}:ready-task",
                task_id=task.task_id,
                actor_ref=context.actor.principal_ref,
                source="onboarding.multi-agent-first-run",
            )
        plan_id = await self._activate_plan(
            context=context,
            task_id=task.task_id,
            workspace_id=workspace_id,
            key=key,
        )
        return await project_first_run_result(
            kernel=self._kernel,
            coordination=self._coordination,
            verification=self._verification,
            agents=agents,
            task_id=task.task_id,
            plan_id=plan_id,
            project_id=project_id,
            workspace_id=workspace_id,
            goal_artifact_id=artifact_id,
        )

    def _validate_request(self, context: RequestContext, resource_ref: str) -> str:
        if resource_ref != FIRST_RUN_RESOURCE_ID:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                f"multi-agent first run requires resource_ref={FIRST_RUN_RESOURCE_ID!r}",
            )
        if context.idempotency_key is None:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "onboarding.run-multi-agent-golden-path requires an idempotency key",
            )
        return context.idempotency_key

    def _require_model(self, context: RequestContext) -> None:
        status = self._onboarding.status(context)
        usable = status.get("usable_golden_path_model_count")
        if isinstance(usable, int) and usable > 0:
            return
        adapters = status.get("installed_model_adapter_ids")
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "No usable local/self-hosted text model is configured for the official first run. "
            "Configure or revalidate one in Onboarding, then retry.",
            details={
                "action": "onboarding.configure-model",
                "installed_model_adapter_ids": adapters if isinstance(adapters, list) else [],
            },
        )

    async def _activate_plan(
        self,
        *,
        context: RequestContext,
        task_id: str,
        workspace_id: str,
        key: str,
    ) -> str:
        proposal = await self._planning.propose(
            task_id=task_id,
            idempotency_key=f"{key}:propose",
            workspace_id=workspace_id,
            task_constraints=(REFERENCE_MULTI_AGENT_CONSTRAINT,),
        )
        if proposal.status is not ProposalStatus.VALIDATED:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "The built-in multi-agent Plan could not be validated. Repair the reported "
                "model or Agent configuration and retry the same goal.",
                details={
                    "proposal_id": proposal.proposal.proposal_id,
                    "validation_errors": list(proposal.validation.errors),
                    "validation_warnings": list(proposal.validation.warnings),
                },
            )
        activated = await self._planning.activate(
            proposal.proposal.proposal_id,
            idempotency_key=f"{key}:activate",
            actor=ActorIdentity(
                context.actor.owner_id or context.actor.principal_ref, ActorType.HUMAN
            ),
        )
        if activated.activation_plan_id is None:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "Validated first-run proposal did not expose an activated canonical Plan.",
            )
        return activated.activation_plan_id


def _required_string(payload: dict[str, JsonValue], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{key} must be a non-blank string")
    return value.strip()


def _optional_string(payload: dict[str, JsonValue], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{key} must be a non-blank string")
    return value.strip()


__all__ = ["MultiAgentFirstRunService", "ONBOARDING_RUN_MULTI_AGENT_GOLDEN_PATH_COMMAND"]
