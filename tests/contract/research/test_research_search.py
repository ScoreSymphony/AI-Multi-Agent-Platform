from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from ai_multi_agent_platform.control_plane import ControlPlane, ControlPlaneHTTP, HTTPRequest
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.research import (
    InMemoryResearchRepository,
    ResearchClass,
    ResearchService,
    ResearchSourceType,
)
from ai_multi_agent_platform.research.models import EvidenceRelation
from ai_multi_agent_platform.research.search import (
    RESEARCH_CLAIM_COLLECTION,
    RESEARCH_EVIDENCE_COLLECTION,
    RESEARCH_ITEM_COLLECTION,
    RESEARCH_OBSERVATION_COLLECTION,
    RESEARCH_SOURCE_COLLECTION,
    register_searchable_research_control_plane,
    research_search_resource_services,
)
from ai_multi_agent_platform.search import document_from_resource
from ai_multi_agent_platform.testing import (
    FakeAuthorizationProvider,
    FakeLifecycleBackend,
    FakeOrchestrator,
)


def run(coro: object) -> object:
    return asyncio.run(coro)  # type: ignore[arg-type]


async def _seed_research() -> tuple[ResearchService, str, str, str, str, str]:
    research = ResearchService(InMemoryResearchRepository())
    project_id = new_id("project")
    item = await research.create_item(
        title="Searchable architecture research",
        question="Which evidence supports the canonical search boundary?",
        research_class=ResearchClass.PROJECT_RESEARCH,
        owner_ref=OwnerRef(type="user", id="research-owner"),
        project_id=project_id,
        data_class="internal",
        metadata={"private_note": "must-never-enter-search"},
    )
    source = await research.add_source(
        item.research_item_id,
        source_type=ResearchSourceType.WEB,
        locator="https://example.test/private/research?token=top-secret",
        title="Canonical search source",
        trust_classification="primary",
        metadata={"provider_payload": "private-source-metadata"},
    )
    observation = await research.observe_source(
        source.source_id,
        retrieved_at=datetime(2026, 9, 8, 7, 0, tzinfo=UTC),
        content_digest="sha256:private-content-digest",
        snapshot_digest="sha256:private-snapshot-digest",
        identity_proven=True,
        metadata={"private_observation": "must-not-be-indexed"},
    )
    claim = await research.add_claim(
        item.research_item_id,
        text="Canonical Search must re-authorize Research results before disclosure.",
        category="architecture",
    )
    evidence = await research.add_evidence(
        claim.claim_id,
        observation.observation_id,
        relation=EvidenceRelation.SUPPORTS,
        location_ref="section:private-location",
        excerpt_digest="sha256:private-excerpt-digest",
    )
    research.assess_claim(claim.claim_id)
    return (
        research,
        item.research_item_id,
        source.source_id,
        observation.observation_id,
        claim.claim_id,
        evidence.evidence_id,
    )


def test_research_search_projections_are_safe_and_reconstructable() -> None:
    async def scenario() -> None:
        research, item_id, source_id, observation_id, claim_id, evidence_id = (
            await _seed_research()
        )
        services = research_search_resource_services(research)

        item = (await services[RESEARCH_ITEM_COLLECTION].list_search_resources())[0]  # type: ignore[attr-defined]
        source = (await services[RESEARCH_SOURCE_COLLECTION].list_search_resources())[0]  # type: ignore[attr-defined]
        observation = (
            await services[RESEARCH_OBSERVATION_COLLECTION].list_search_resources()  # type: ignore[attr-defined]
        )[0]
        claim = (await services[RESEARCH_CLAIM_COLLECTION].list_search_resources())[0]  # type: ignore[attr-defined]
        evidence = (
            await services[RESEARCH_EVIDENCE_COLLECTION].list_search_resources()  # type: ignore[attr-defined]
        )[0]

        assert item["id"] == item_id
        assert item["owner_id"] == "research-owner"
        assert item["summary"] == "Which evidence supports the canonical search boundary?"
        assert source["id"] == source_id
        assert source["status"] == "current"
        assert observation["id"] == observation_id
        assert observation["status"] == "current"
        assert claim["id"] == claim_id
        assert claim["status"] == "supported"
        assert evidence["id"] == evidence_id
        assert evidence["status"] == "current"

        serialized = repr((item, source, observation, claim, evidence))
        for forbidden in (
            "top-secret",
            "private/research",
            "private-content-digest",
            "private-snapshot-digest",
            "private-excerpt-digest",
            "private-source-metadata",
            "must-never-enter-search",
            "must-not-be-indexed",
            "section:private-location",
        ):
            assert forbidden not in serialized

        claim_document = document_from_resource(
            claim,
            collection=RESEARCH_CLAIM_COLLECTION,
        )
        assert claim_document.resource_type == "research-claim"
        assert claim_document.resource_id == claim_id
        assert claim_document.owner_id == "research-owner"
        assert claim_document.project_id == item["project_id"]
        assert claim_document.status == "supported"
        assert claim_document.canonical_ref == f"/api/v1/research-claims/{claim_id}"
        assert "architecture" in claim_document.keywords

        evidence_document = document_from_resource(
            evidence,
            collection=RESEARCH_EVIDENCE_COLLECTION,
        )
        assert evidence_document.status == "current"
        assert claim_id in evidence_document.keywords
        assert source_id in evidence_document.keywords
        assert observation_id in evidence_document.keywords

    asyncio.run(scenario())


def test_research_search_rechecks_owner_scope_before_disclosure() -> None:
    async def scenario() -> None:
        research, _, _, _, claim_id, _ = await _seed_research()
        events = InMemoryKernelRepository()
        kernel = PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=FakeLifecycleBackend(),
            repository=events,
        )
        control_plane = ControlPlane(
            kernel=kernel,
            events=events,
            authorization=FakeAuthorizationProvider(),
        )
        register_searchable_research_control_plane(control_plane, research)
        http = ControlPlaneHTTP(control_plane)

        assert await control_plane.rebuild_search_index() == 5

        owner_result = await http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/search",
                query={"type": "research-claim", "id": claim_id},
                headers={
                    "X-Principal-Ref": "user:research-owner",
                    "X-Owner-Type": "user",
                    "X-Owner-Id": "research-owner",
                },
            )
        )
        assert owner_result.status == 200
        assert isinstance(owner_result.body, dict)
        assert owner_result.body["total"] == 1
        assert owner_result.body["items"][0]["resource_id"] == claim_id

        stranger_result = await http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/search",
                query={"type": "research-claim", "id": claim_id},
                headers={
                    "X-Principal-Ref": "user:other-user",
                    "X-Owner-Type": "user",
                    "X-Owner-Id": "other-user",
                },
            )
        )
        assert stranger_result.status == 200
        assert isinstance(stranger_result.body, dict)
        assert stranger_result.body["total"] == 0
        assert claim_id not in repr(stranger_result.body)

    asyncio.run(scenario())
