from __future__ import annotations

from ai_multi_agent_platform.control_plane.extensions import ControlPlane
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.research.control_plane import (
    RESEARCH_CLAIM_COLLECTION,
    RESEARCH_COMMANDS,
    RESEARCH_EVIDENCE_COLLECTION,
    RESEARCH_ITEM_COLLECTION,
    RESEARCH_OBSERVATION_COLLECTION,
    RESEARCH_SOURCE_COLLECTION,
)
from ai_multi_agent_platform.research.repository import InMemoryResearchRepository
from ai_multi_agent_platform.research.search import register_searchable_research_control_plane
from ai_multi_agent_platform.research.service import ResearchService
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator

RESEARCH_COLLECTIONS = (
    RESEARCH_ITEM_COLLECTION,
    RESEARCH_SOURCE_COLLECTION,
    RESEARCH_OBSERVATION_COLLECTION,
    RESEARCH_CLAIM_COLLECTION,
    RESEARCH_EVIDENCE_COLLECTION,
)


def test_searchable_research_surface_has_one_explicit_owner() -> None:
    repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
    )
    control_plane = ControlPlane(kernel=kernel, events=repository)
    research = ResearchService(InMemoryResearchRepository())

    register_searchable_research_control_plane(control_plane, research)

    assert "research" in control_plane.registered_modules
    for collection in RESEARCH_COLLECTIONS:
        assert control_plane.resource_owner(collection) == "research"
    for command in RESEARCH_COMMANDS:
        assert control_plane.command_owner(command) == "research"
