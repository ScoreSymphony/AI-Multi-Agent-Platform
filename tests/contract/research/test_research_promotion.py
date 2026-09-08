from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from ai_multi_agent_platform.contracts import OperationContext
from ai_multi_agent_platform.data import (
    DataAccessContext,
    LocalKnowledgeProvider,
    LocalMemoryProvider,
    MemoryScope,
    RetentionPolicy,
)
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.research import (
    EvidenceRelation,
    InMemoryResearchRepository,
    ResearchClass,
    ResearchPromotionBridge,
    ResearchService,
    ResearchSourceType,
)

OWNER = OwnerRef(type="user", id="researcher")


def run(coro: object) -> object:
    return asyncio.run(coro)  # type: ignore[arg-type]


def test_explicit_memory_and_knowledge_promotion_preserve_research_provenance(
    tmp_path: Path,
) -> None:
    task_id = new_id("task")
    research = ResearchService(InMemoryResearchRepository())
    item = run(
        research.create_item(
            title="Promotion research",
            question="Can verified source-backed work be promoted with provenance?",
            research_class=ResearchClass.TASK_RESEARCH,
            owner_ref=OWNER,
            task_id=task_id,
        )
    )
    source = run(
        research.add_source(
            item.research_item_id,
            source_type=ResearchSourceType.DOCUMENT,
            locator="file:source-document",
            title="Source document",
        )
    )
    observation = run(
        research.observe_source(
            source.source_id,
            retrieved_at=datetime(2026, 9, 8, 10, tzinfo=UTC),
            content_digest="sha256:source-document",
            identity_proven=True,
        )
    )
    claim = run(
        research.add_claim(
            item.research_item_id,
            text="The source supports the promoted statement.",
            category="knowledge",
        )
    )
    evidence = run(
        research.add_evidence(
            claim.claim_id,
            observation.observation_id,
            relation=EvidenceRelation.SUPPORTS,
        )
    )
    research.assess_claim(claim.claim_id)

    access = DataAccessContext(
        operation=OperationContext(
            correlation_id="research-promotion",
            owner_type="user",
            owner_id="researcher",
        ),
        actor_ref="user:researcher",
        task_id=task_id,
    )
    memory = LocalMemoryProvider(tmp_path / "memory.sqlite3")
    knowledge = LocalKnowledgeProvider(tmp_path / "knowledge.sqlite3")
    bridge = ResearchPromotionBridge(research, memory=memory, knowledge=knowledge)

    promoted_memory = run(
        bridge.promote_to_memory(
            item.research_item_id,
            value={"statement": "promoted"},
            scope=MemoryScope.TASK,
            scope_id=task_id,
            retention=RetentionPolicy.TASK_LIFETIME,
            access=access,
            created_by="service:research-promotion",
            require_verification=False,
        )
    )
    assert promoted_memory.metadata["research_item_id"] == item.research_item_id
    assert promoted_memory.metadata["research_claim_ids"] == [claim.claim_id]
    assert promoted_memory.metadata["research_evidence_ids"] == [evidence.evidence_id]
    assert {entry.kind for entry in promoted_memory.provenance} >= {
        "research_item",
        "research_claim",
        "research_evidence",
    }

    promoted_knowledge = run(
        bridge.promote_to_knowledge(
            item.research_item_id,
            content="promoted source-backed statement",
            location="research://promotion",
            access=access,
            created_by="service:research-promotion",
            require_verification=False,
        )
    )
    assert promoted_knowledge.metadata["research_item_id"] == item.research_item_id
    assert promoted_knowledge.metadata["research_claim_ids"] == [claim.claim_id]
    assert promoted_knowledge.metadata["research_evidence_ids"] == [evidence.evidence_id]
