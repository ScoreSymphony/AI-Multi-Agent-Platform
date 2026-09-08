from __future__ import annotations

import asyncio
from typing import Any

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, JsonValue
from ai_multi_agent_platform.control_plane.models import ActorContext, PageQuery, RequestContext
from ai_multi_agent_platform.research import InMemoryResearchRepository, ResearchService
from ai_multi_agent_platform.research.control_plane import (
    RESEARCH_CLAIM_COLLECTION,
    RESEARCH_COLLECTIONS,
    RESEARCH_COMMANDS,
    RESEARCH_EVIDENCE_COLLECTION,
    RESEARCH_ITEM_COLLECTION,
    RESEARCH_OBSERVATION_COLLECTION,
    RESEARCH_SOURCE_COLLECTION,
    register_research_control_plane,
)


class _CapturingControlPlane:
    def __init__(self) -> None:
        self.resources: dict[str, Any] = {}
        self.commands: dict[str, Any] = {}

    def register_resource_service(self, collection: str, service: object) -> None:
        self.resources[collection] = service

    def register_command(self, command: str, handler: object) -> None:
        self.commands[command] = handler


def run(coro: object) -> object:
    return asyncio.run(coro)  # type: ignore[arg-type]


def _context(*, owner_id: str = "researcher", key: str = "idem-1") -> RequestContext:
    return RequestContext(
        request_id=f"request-{key}",
        correlation_id="research-control-plane",
        idempotency_key=key,
        actor=ActorContext(
            principal_ref=f"user:{owner_id}",
            owner_type="user",
            owner_id=owner_id,
            actor_type="human",
        ),
    )


def _invoke(
    control_plane: _CapturingControlPlane,
    command: str,
    context: RequestContext,
    resource_ref: str,
    payload: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    result = run(control_plane.commands[command](context, resource_ref, payload))
    assert isinstance(result, dict)
    return result


def test_registration_exposes_canonical_research_resources_and_commands() -> None:
    control_plane = _CapturingControlPlane()
    register_research_control_plane(  # type: ignore[arg-type]
        control_plane, ResearchService(InMemoryResearchRepository())
    )

    assert set(control_plane.resources) == set(RESEARCH_COLLECTIONS)
    assert set(control_plane.commands) == set(RESEARCH_COMMANDS)


def test_control_plane_roundtrip_exposes_exact_source_and_freshness_provenance() -> None:
    research = ResearchService(InMemoryResearchRepository())
    control_plane = _CapturingControlPlane()
    register_research_control_plane(control_plane, research)  # type: ignore[arg-type]
    owner = _context()

    item = _invoke(
        control_plane,
        "research.create",
        owner,
        RESEARCH_ITEM_COLLECTION,
        {
            "title": "Research API",
            "question": "Which source revision supports this claim?",
            "research_class": "project_research",
            "freshness_policy": {"revalidate_on_source_change": True},
        },
    )
    item_id = str(item["id"])

    source = _invoke(
        control_plane,
        "research.source.add",
        _context(key="source-add"),
        item_id,
        {
            "source_type": "web",
            "locator": "https://example.test/research",
            "title": "Primary source",
        },
    )
    source_id = str(source["id"])

    first_observation = _invoke(
        control_plane,
        "research.source.observe",
        _context(key="observe-v1"),
        source_id,
        {
            "retrieved_at": "2026-09-08T01:00:00+00:00",
            "content_digest": "sha256:first",
            "snapshot_digest": "sha256:snapshot-first",
            "identity_proven": True,
        },
    )
    observation_id = str(first_observation["id"])

    claim = _invoke(
        control_plane,
        "research.claim.add",
        _context(key="claim-add"),
        item_id,
        {
            "text": "The primary source documents the behavior.",
            "category": "architecture",
            "confidence": "high",
        },
    )
    claim_id = str(claim["id"])

    evidence = _invoke(
        control_plane,
        "research.evidence.add",
        _context(key="evidence-add"),
        claim_id,
        {
            "source_observation_id": observation_id,
            "relation": "supports",
            "location_ref": "section:behavior",
        },
    )
    evidence_id = str(evidence["id"])
    research.assess_claim(claim_id)

    changed = _invoke(
        control_plane,
        "research.source.observe",
        _context(key="observe-v2"),
        source_id,
        {
            "retrieved_at": "2026-09-08T02:00:00+00:00",
            "content_digest": "sha256:changed",
            "snapshot_digest": "sha256:snapshot-changed",
            "identity_proven": True,
        },
    )
    assert changed["state"] == "changed"

    evidence_view = run(
        control_plane.resources[RESEARCH_EVIDENCE_COLLECTION].get_resource(owner, evidence_id)
    )
    assert isinstance(evidence_view, dict)
    assert evidence_view["freshness"] == "stale"
    assert evidence_view["source_observation_id"] == observation_id
    assert evidence_view["source_content_digest"] == "sha256:first"
    assert evidence_view["source_snapshot_digest"] == "sha256:snapshot-first"

    source_view = run(
        control_plane.resources[RESEARCH_SOURCE_COLLECTION].get_resource(owner, source_id)
    )
    assert isinstance(source_view, dict)
    assert source_view["current_observation_state"] == "changed"
    assert source_view["current_binding"]["content_digest"] == "sha256:changed"

    observations = run(
        control_plane.resources[RESEARCH_OBSERVATION_COLLECTION].list_resources(owner, PageQuery())
    )
    assert isinstance(observations, tuple)
    assert len(observations) == 2
    assert observations[0]["content_digest"] == "sha256:first"
    assert observations[1]["content_digest"] == "sha256:changed"

    item_view = run(control_plane.resources[RESEARCH_ITEM_COLLECTION].get_resource(owner, item_id))
    assert isinstance(item_view, dict)
    assert item_view["evidence_freshness"][evidence_id] == "stale"
    assert item_view["task_id"] is None
    assert item_view["plan_id"] is None

    claim_view = run(
        control_plane.resources[RESEARCH_CLAIM_COLLECTION].get_resource(owner, claim_id)
    )
    assert isinstance(claim_view, dict)
    assert claim_view["status"] == "supported"
    assert claim_view["evidence_ids"] == [evidence_id]


def test_control_plane_revalidation_creates_new_evidence_without_rewriting_history() -> None:
    research = ResearchService(InMemoryResearchRepository())
    control_plane = _CapturingControlPlane()
    register_research_control_plane(control_plane, research)  # type: ignore[arg-type]

    item = _invoke(
        control_plane,
        "research.create",
        _context(),
        RESEARCH_ITEM_COLLECTION,
        {
            "title": "Revalidation",
            "question": "Did the source change?",
            "research_class": "task_research",
        },
    )
    item_id = str(item["id"])
    source = _invoke(
        control_plane,
        "research.source.add",
        _context(key="source"),
        item_id,
        {"source_type": "web", "locator": "https://example.test", "title": "Source"},
    )
    source_id = str(source["id"])
    first = _invoke(
        control_plane,
        "research.source.observe",
        _context(key="first"),
        source_id,
        {
            "retrieved_at": "2026-09-08T01:00:00Z",
            "content_digest": "sha256:first",
            "identity_proven": True,
        },
    )
    claim = _invoke(
        control_plane,
        "research.claim.add",
        _context(key="claim"),
        item_id,
        {"text": "Version one supports this.", "category": "technical"},
    )
    evidence = _invoke(
        control_plane,
        "research.evidence.add",
        _context(key="evidence"),
        str(claim["id"]),
        {"source_observation_id": str(first["id"]), "relation": "supports"},
    )
    original_id = str(evidence["id"])

    second = _invoke(
        control_plane,
        "research.source.observe",
        _context(key="second"),
        source_id,
        {
            "retrieved_at": "2026-09-08T02:00:00Z",
            "content_digest": "sha256:second",
            "identity_proven": True,
        },
    )
    replacement = _invoke(
        control_plane,
        "research.evidence.revalidate",
        _context(key="revalidate"),
        original_id,
        {"source_observation_id": str(second["id"])},
    )

    assert replacement["id"] != original_id
    assert replacement["supersedes_evidence_id"] == original_id
    assert replacement["source_content_digest"] == "sha256:second"
    assert research.repository.get_evidence(original_id).source_content_digest == "sha256:first"


def test_control_plane_owner_visibility_fails_closed() -> None:
    research = ResearchService(InMemoryResearchRepository())
    control_plane = _CapturingControlPlane()
    register_research_control_plane(control_plane, research)  # type: ignore[arg-type]
    owner = _context()
    stranger = _context(owner_id="other", key="other")

    item = _invoke(
        control_plane,
        "research.create",
        owner,
        RESEARCH_ITEM_COLLECTION,
        {
            "title": "Private research",
            "question": "Who can inspect this?",
            "research_class": "project_research",
        },
    )
    item_id = str(item["id"])

    hidden = run(
        control_plane.resources[RESEARCH_ITEM_COLLECTION].list_resources(stranger, PageQuery())
    )
    assert hidden == ()

    with pytest.raises(ContractError) as denied:
        run(control_plane.resources[RESEARCH_ITEM_COLLECTION].get_resource(stranger, item_id))
    assert denied.value.code is ErrorCode.NOT_FOUND
