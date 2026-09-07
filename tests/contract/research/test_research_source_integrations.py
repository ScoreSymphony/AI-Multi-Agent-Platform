from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import cast

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.repository_intelligence.models import (
    RepositoryIntelligenceFreshness,
    RepositoryIntelligenceProvenance,
)
from ai_multi_agent_platform.research import (
    InMemoryResearchRepository,
    ResearchClass,
    ResearchService,
    ResearchSourceType,
    SourceObservationState,
)
from ai_multi_agent_platform.security import AuthorizationGate

OWNER = OwnerRef(type="user", id="researcher")


def run(coro: object) -> object:
    return asyncio.run(coro)  # type: ignore[arg-type]


def test_repository_intelligence_preserves_exact_existing_provenance() -> None:
    research = ResearchService(InMemoryResearchRepository())
    item = run(
        research.create_item(
            title="Repository research",
            question="What does this exact repository revision contain?",
            research_class=ResearchClass.PROJECT_RESEARCH,
            owner_ref=OWNER,
        )
    )
    source = run(
        research.add_source(
            item.research_item_id,
            source_type=ResearchSourceType.REPOSITORY_INTELLIGENCE,
            locator="repository:example/project",
            title="Example repository",
        )
    )
    first_provenance = RepositoryIntelligenceProvenance(
        repository_id="repository:example/project",
        requested_revision="main",
        resolved_revision="a" * 40,
        intelligence_provider_id="repository-intelligence:reference",
        freshness=RepositoryIntelligenceFreshness.LIVE_REVISION,
    )
    first = run(
        research.observe_repository_intelligence(
            source.source_id,
            first_provenance,
            retrieved_at=datetime(2026, 9, 8, 10, tzinfo=UTC),
            content_digest="sha256:first",
        )
    )
    assert first.state is SourceObservationState.CURRENT
    assert first.repository_id == first_provenance.repository_id
    assert first.requested_repository_revision == first_provenance.requested_revision
    assert first.resolved_repository_revision == first_provenance.resolved_revision
    assert first.intelligence_provider_id == first_provenance.intelligence_provider_id
    assert first.metadata["freshness"] == first_provenance.freshness.value

    changed_provenance = RepositoryIntelligenceProvenance(
        repository_id=first_provenance.repository_id,
        requested_revision="main",
        resolved_revision="b" * 40,
        intelligence_provider_id=first_provenance.intelligence_provider_id,
        freshness=RepositoryIntelligenceFreshness.LIVE_REVISION,
    )
    changed = run(
        research.observe_repository_intelligence(
            source.source_id,
            changed_provenance,
            retrieved_at=datetime(2026, 9, 8, 11, tzinfo=UTC),
            content_digest="sha256:second",
        )
    )
    assert changed.state is SourceObservationState.CHANGED
    assert changed.observation_id != first.observation_id
    assert research.repository.get_observation(first.observation_id) == first


def test_configured_authorization_fails_closed_without_actor_context() -> None:
    research = ResearchService(
        InMemoryResearchRepository(),
        authorization=cast(AuthorizationGate, object()),
    )
    with pytest.raises(ContractError) as denied:
        run(
            research.create_item(
                title="Denied research",
                question="Should missing identity bypass policy?",
                research_class=ResearchClass.PROJECT_RESEARCH,
                owner_ref=OWNER,
            )
        )
    assert denied.value.code is ErrorCode.FORBIDDEN
