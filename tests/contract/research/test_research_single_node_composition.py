from __future__ import annotations

import asyncio
from pathlib import Path

from ai_multi_agent_platform.contracts import OperationContext
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.research import ResearchClass
from ai_multi_agent_platform.research.deployment import compose_single_node_research
from ai_multi_agent_platform.security import ActorIdentity, ActorType


def test_single_node_research_is_public_durable_authorized_and_search_registered(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        config = SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False)
        first = build_single_node_deployment(config)
        admin = first.bootstrap_admin("admin", "research-composition-password")
        composition = compose_single_node_research(first)
        assert composition.service is first.research
        assert composition.database_path == config.database_dir / "research.sqlite3"

        actor = ActorIdentity(admin.user_id, ActorType.HUMAN)
        operation = OperationContext(
            correlation_id="research-single-node-create",
            owner_type="user",
            owner_id=admin.user_id,
        )
        item = await first.research.create_item(
            title="Durable single-node Research",
            question="Does canonical Research survive a standard single-node restart?",
            research_class=ResearchClass.DOMAIN_RESEARCH,
            owner_ref=OwnerRef(type="user", id=admin.user_id),
            actor=actor,
            operation=operation,
        )
        indexed = await first.control_plane.rebuild_search_index()
        assert indexed >= 1

        second = build_single_node_deployment(config)
        restored = compose_single_node_research(second)
        assert restored.service is second.research
        assert second.research.repository.get_item(item.research_item_id) == item
        rebuilt = await second.control_plane.rebuild_search_index()
        assert rebuilt >= 1

    asyncio.run(scenario())
