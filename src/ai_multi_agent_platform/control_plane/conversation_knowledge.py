"""Canonical Knowledge-reference validation for Conversation messages (#72)."""

from __future__ import annotations

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import OperationContext, OperationControl
from ai_multi_agent_platform.conversations import Conversation, ReferenceKind, ResourceReference
from ai_multi_agent_platform.data import DataAccessContext, KnowledgeProvider

from .models import RequestContext


async def validate_conversation_knowledge_reference(
    provider: KnowledgeProvider | None,
    context: RequestContext,
    conversation: Conversation,
    reference: ResourceReference,
) -> None:
    """Validate one canonical knowledge-source reference through the public data boundary.

    Conversation persists only the canonical ``knowledge_source_*`` identity. Provider-native
    index identity, document content and embeddings remain wholly owned by the Knowledge
    domain.
    """

    if reference.kind is not ReferenceKind.KNOWLEDGE:
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "Knowledge validation requires a canonical knowledge reference",
        )
    if provider is None:
        raise ContractError(
            ErrorCode.UNAVAILABLE,
            "canonical Knowledge provider is not configured for conversation references",
            retryable=True,
        )

    access = DataAccessContext(
        operation=OperationContext(
            correlation_id=context.correlation_id,
            owner_type=context.actor.owner_type,
            owner_id=context.actor.owner_id,
            project_id=conversation.project_id,
            control=OperationControl(idempotency_key=context.idempotency_key),
        ),
        actor_ref=context.actor.principal_ref,
        audit_metadata=dict(context.actor.trust_context),
    )
    index = await provider.get_index_status(reference.id, access)
    if index.source_id != reference.id:
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "Knowledge provider resolved a different canonical source id",
            details={
                "requested_source_id": reference.id,
                "resolved_source_id": index.source_id,
            },
        )


__all__ = ["validate_conversation_knowledge_reference"]
