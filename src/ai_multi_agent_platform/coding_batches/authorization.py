"""Exact-action #15 authorization composition for coding-batch integration."""

from __future__ import annotations

from dataclasses import dataclass

from ai_multi_agent_platform.contracts import OperationContext
from ai_multi_agent_platform.security import (
    ActorIdentity,
    AuthorizationAction,
    AuthorizationContext,
    AuthorizationGate,
    ProposedAction,
    ResourceType,
    RiskClassification,
)

from .models import CodingBatch, IntegrationCandidate, IntegrationState
from .service import CodingBatchCoordinator


@dataclass(frozen=True, slots=True)
class CodingBatchAuthorizationContext:
    """Caller identity and correlation context used by canonical #15 authorization."""

    actor: ActorIdentity
    operation: OperationContext
    approval_id: str | None = None


class AuthorizedCodingBatchIntegration:
    """Gate merge readiness through #15 without making #872 a merge authority.

    The composed action binds authorization/Approval to the exact integration candidate and
    combined revision. Repository push/PR/merge side effects remain independently enforced by
    the canonical #82 RepositoryService.
    """

    def __init__(
        self,
        coordinator: CodingBatchCoordinator,
        authorization: AuthorizationGate,
    ) -> None:
        self._coordinator = coordinator
        self._authorization = authorization

    def merge_ready_action(
        self,
        batch: CodingBatch,
        candidate: IntegrationCandidate,
        context: CodingBatchAuthorizationContext,
    ) -> ProposedAction:
        if candidate.state is not IntegrationState.VALIDATED or candidate.validation is None:
            raise ValueError("integration requires fresh combined validation before authorization")
        if candidate.integrated_revision is None:
            raise ValueError("validated integration candidate is missing integrated revision")
        return ProposedAction(
            AuthorizationContext(
                actor=context.actor,
                action=AuthorizationAction.MODIFY,
                resource_type=ResourceType.GENERIC,
                resource_id=candidate.integration_id,
                operation=context.operation,
                side_effect="coding_batch_merge_readiness",
                security_labels=("repository", "coding_batch", "merge_readiness"),
            ),
            payload={
                "batch_id": batch.batch_id,
                "repository_id": batch.repository_id,
                "target_ref": batch.target_ref,
                "target_base_revision": candidate.target_base_revision,
                "integration_id": candidate.integration_id,
                "ordered_workstream_ids": list(candidate.ordered_workstream_ids),
                "ordered_revisions": list(candidate.ordered_revisions),
                "integrated_revision": candidate.integrated_revision,
                "verification_id": candidate.validation.verification_id,
            },
        )

    async def mark_merge_ready(
        self,
        batch_id: str,
        integration_id: str,
        *,
        context: CodingBatchAuthorizationContext,
    ) -> IntegrationCandidate:
        batch = self._coordinator.get(batch_id)
        candidate = batch.integration_candidate(integration_id)
        action = self.merge_ready_action(batch, candidate, context)
        await self._authorization.enforce(
            action,
            approval_id=context.approval_id,
            risk=RiskClassification.HIGH,
        )
        return self._coordinator.mark_merge_ready(
            batch_id,
            integration_id,
            authorization_granted=True,
        )
