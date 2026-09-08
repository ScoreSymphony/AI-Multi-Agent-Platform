"""Governed Research -> Planning/Knowledge/Memory integration boundaries."""

from __future__ import annotations

from datetime import UTC, datetime

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, JsonValue
from ai_multi_agent_platform.data.contracts import KnowledgeProvider, MemoryProvider
from ai_multi_agent_platform.data.models import (
    DataAccessContext,
    KnowledgeSource,
    KnowledgeStatus,
    MemoryEntry,
    MemoryOrigin,
    MemoryScope,
    RetentionPolicy,
    SourceRef,
    new_knowledge_source_id,
    new_memory_id,
)
from ai_multi_agent_platform.planning import PlanningService, PlanningTrigger, ProposalRecord

from .models import ResearchActionContext
from .service import ResearchService


class ResearchPlanningBridge:
    """Pass exact Research provenance into #439 rather than mutating Plans directly."""

    def __init__(self, research: ResearchService, planning: PlanningService) -> None:
        self.research = research
        self.planning = planning

    async def propose(
        self,
        research_item_id: str,
        *,
        task_id: str,
        idempotency_key: str,
        require_verification: bool = True,
        trigger: PlanningTrigger = PlanningTrigger.INITIAL,
        reason: str | None = None,
        workspace_id: str | None = None,
        task_constraints: tuple[str, ...] = (),
        granted_permissions: frozenset[str] = frozenset(),
        available_worker_capabilities: frozenset[str] = frozenset(),
    ) -> ProposalRecord:
        item = self.research.repository.get_item(research_item_id)
        if item.task_id is not None and item.task_id != task_id:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "Research Item is bound to a different canonical Task",
            )
        context = self.research.build_action_context(
            research_item_id,
            require_verification=require_verification,
        )
        return await self.planning.propose(
            task_id=task_id,
            idempotency_key=idempotency_key,
            trigger=trigger,
            reason=reason,
            workspace_id=workspace_id or item.workspace_id,
            evidence_refs=context.planning_evidence_refs(),
            task_constraints=task_constraints,
            granted_permissions=granted_permissions,
            available_worker_capabilities=available_worker_capabilities,
        )


class ResearchPromotionBridge:
    """Explicitly promote verified/source-backed Research into mutable data domains."""

    def __init__(
        self,
        research: ResearchService,
        *,
        knowledge: KnowledgeProvider | None = None,
        memory: MemoryProvider | None = None,
    ) -> None:
        self.research = research
        self.knowledge = knowledge
        self.memory = memory

    async def promote_to_knowledge(
        self,
        research_item_id: str,
        *,
        content: str,
        location: str,
        access: DataAccessContext,
        created_by: str,
        require_verification: bool = True,
    ) -> KnowledgeSource:
        if self.knowledge is None:
            raise ContractError(ErrorCode.UNAVAILABLE, "Knowledge provider is not configured")
        action = self.research.build_action_context(
            research_item_id,
            require_verification=require_verification,
        )
        item = self.research.repository.get_item(research_item_id)
        now = datetime.now(UTC)
        source = KnowledgeSource(
            source_id=new_knowledge_source_id(),
            project_id=item.project_id,
            owner_ref=f"{item.owner_ref.type}:{item.owner_ref.id}",
            created_by=created_by,
            title=item.title,
            revision=f"research:{item.revision}:{item.digest}",
            status=KnowledgeStatus.REGISTERED,
            created_at=now,
            updated_at=now,
            metadata=_promotion_metadata(action),
        )
        registered = await self.knowledge.register_source(source, access)
        await self.knowledge.ingest_source(registered.source_id, content, location, access)
        return registered

    async def promote_to_memory(
        self,
        research_item_id: str,
        *,
        value: JsonValue,
        scope: MemoryScope,
        scope_id: str,
        retention: RetentionPolicy,
        access: DataAccessContext,
        created_by: str,
        require_verification: bool = True,
    ) -> MemoryEntry:
        if self.memory is None:
            raise ContractError(ErrorCode.UNAVAILABLE, "Memory provider is not configured")
        action = self.research.build_action_context(
            research_item_id,
            require_verification=require_verification,
        )
        item = self.research.repository.get_item(research_item_id)
        provenance = (
            SourceRef(
                kind="research_item",
                ref=item.research_item_id,
                revision=str(item.revision),
            ),
            *(SourceRef(kind="research_claim", ref=value) for value in action.claim_ids),
            *(SourceRef(kind="research_evidence", ref=value) for value in action.evidence_ids),
        )
        entry = MemoryEntry(
            memory_id=new_memory_id(),
            scope=scope,
            scope_id=scope_id,
            owner_ref=f"{item.owner_ref.type}:{item.owner_ref.id}",
            created_by=created_by,
            value=value,
            created_at=datetime.now(UTC),
            retention=retention,
            origin=MemoryOrigin.AGENT_DERIVED,
            provenance=provenance,
            classification=_promotion_classification(item.data_class),
            metadata=_promotion_metadata(action),
        )
        return await self.memory.write_entry(entry, access)


def _promotion_classification(value: str) -> str:
    # ``standard`` is legacy Research vocabulary from before the canonical
    # platform-wide classification contract. Preserve old durable items without
    # weakening them by mapping the legacy default to INTERNAL.
    return "internal" if value == "standard" else value


def _promotion_metadata(action: ResearchActionContext) -> dict[str, JsonValue]:
    return {
        "research_item_id": action.research_item_id,
        "research_item_revision": action.research_item_revision,
        "research_item_digest": action.research_item_digest,
        "research_claim_ids": list(action.claim_ids),
        "research_evidence_ids": list(action.evidence_ids),
        "verification_ids": list(action.verification_ids),
    }
