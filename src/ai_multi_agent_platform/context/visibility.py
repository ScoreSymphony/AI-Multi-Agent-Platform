"""Authorization-backed source visibility for Context Bundle Control Plane projections."""

from __future__ import annotations

from ai_multi_agent_platform.contracts import (
    AuthorizationOutcome,
    AuthorizationProvider,
    AuthorizationRequest,
    OperationContext,
    normalize_authorization_decision,
)
from ai_multi_agent_platform.control_plane.models import RequestContext

from .models import ContextBundle, ContextEntry, ContextSourceType

_RESOURCE_TYPES: dict[ContextSourceType, str] = {
    ContextSourceType.SYSTEM_SECURITY: "administrative_settings",
    ContextSourceType.TASK: "task",
    ContextSourceType.PLAN_STEP: "task",
    ContextSourceType.AGENT: "agent",
    ContextSourceType.SKILL: "generic",
    ContextSourceType.MEMORY: "memory",
    ContextSourceType.KNOWLEDGE: "knowledge_source",
    ContextSourceType.RESEARCH_EVIDENCE: "generic",
    ContextSourceType.REPOSITORY: "workspace",
    ContextSourceType.FILE: "file",
    ContextSourceType.ARTIFACT: "artifact",
    ContextSourceType.RESULT: "generic",
    ContextSourceType.PRIOR_RUN: "run",
    ContextSourceType.VERIFICATION: "generic",
    ContextSourceType.HUMAN: "generic",
    ContextSourceType.AGENT_HANDOFF: "generic",
}


class AuthorizationContextEntryVisibilityResolver:
    """Re-authorize each Context source before exposing its provenance to a viewer.

    Reading the Context Bundle resource is intentionally insufficient. Source IDs, revisions,
    locators and omission details remain hidden unless the caller can independently read the
    underlying canonical source through #15.
    """

    def __init__(self, authorization: AuthorizationProvider) -> None:
        self.authorization = authorization

    async def can_view(
        self,
        context: RequestContext,
        bundle: ContextBundle,
        entry: ContextEntry,
    ) -> bool:
        owner_type = context.actor.owner_type
        owner_id = context.actor.owner_id
        operation = OperationContext(
            correlation_id=context.correlation_id,
            owner_type=owner_type,
            owner_id=owner_id,
            project_id=entry.project_id,
        )
        decision = normalize_authorization_decision(
            await self.authorization.authorize(
                AuthorizationRequest(
                    principal_ref=context.actor.principal_ref,
                    actor_type=context.actor.actor_type or owner_type or "human",
                    action="read",
                    resource_type=_RESOURCE_TYPES[entry.source.source_type],
                    resource_ref=entry.source.source_id,
                    context=operation,
                    workspace_id=entry.workspace_id,
                    task_id=bundle.task_id,
                    run_id=bundle.run_id,
                    agent_id=bundle.agent_id,
                    security_labels=entry.security_labels,
                    trust_context={
                        "context_inspection": True,
                        "context_source_type": entry.source.source_type.value,
                        "data_classification": entry.data_classification.value,
                    },
                )
            )
        )
        return decision.outcome is AuthorizationOutcome.ALLOW


__all__ = ["AuthorizationContextEntryVisibilityResolver"]
