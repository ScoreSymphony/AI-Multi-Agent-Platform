from __future__ import annotations

import asyncio

from ai_multi_agent_platform.agents import InMemoryAgentRepository
from ai_multi_agent_platform.control_plane import ScopeStore
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.models import ModelRegistry
from ai_multi_agent_platform.portability import (
    RESEARCH_BUNDLE_RESOURCE_TYPE,
    ExportSelection,
    IdPolicy,
)
from ai_multi_agent_platform.portability.composition import build_agent_portability_workflow
from ai_multi_agent_platform.research import (
    InMemoryResearchRepository,
    ResearchClass,
    ResearchService,
)


def test_shared_portability_composition_keeps_research_export_disabled_by_default() -> None:
    research = ResearchService(InMemoryResearchRepository())
    workflow = build_agent_portability_workflow(
        agents=InMemoryAgentRepository(),
        models=ModelRegistry(),
        scopes=ScopeStore(),
        platform_version="0.0.1",
        research=research,
    )

    assert RESEARCH_BUNDLE_RESOURCE_TYPE not in workflow.export_resource_types


def test_shared_portability_composition_exports_research_history_when_explicitly_enabled() -> None:
    async def scenario() -> None:
        research = ResearchService(InMemoryResearchRepository())
        item = await research.create_item(
            title="Shared portable Research",
            question="Is Research registered in the shared #79 composition?",
            research_class=ResearchClass.PROJECT_RESEARCH,
            owner_ref=OwnerRef(type="user", id="portable-research-owner"),
        )
        workflow = build_agent_portability_workflow(
            agents=InMemoryAgentRepository(),
            models=ModelRegistry(),
            scopes=ScopeStore(),
            platform_version="0.0.1",
            research=research,
            research_export_enabled=True,
        )

        exported = await workflow.export_package(
            (ExportSelection(RESEARCH_BUNDLE_RESOURCE_TYPE, item.research_item_id),)
        )

        assert len(exported.package.resources) == 1
        resource = exported.package.resources[0]
        assert resource.resource_type == RESEARCH_BUNDLE_RESOURCE_TYPE
        assert resource.resource_id == item.research_item_id
        assert resource.id_policy is IdPolicy.HISTORICAL_PRESERVE
        assert resource.payload["bundle"]["activation_semantics"] == "none"

    asyncio.run(scenario())
