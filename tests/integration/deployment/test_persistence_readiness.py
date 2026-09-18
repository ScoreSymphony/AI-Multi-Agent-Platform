from __future__ import annotations

import asyncio
from pathlib import Path

from ai_multi_agent_platform.deployment import (
    SingleNodeConfig,
    build_single_node_deployment,
)
from ai_multi_agent_platform.deployment.single_node import (
    build_single_node_deployment as build_base_single_node_deployment,
)


def test_required_persistence_outage_blocks_readiness_until_store_returns(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        config = SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False)
        deployment = build_single_node_deployment(config)

        healthy = await deployment.control_plane.health()
        assert healthy["ready"] is True

        required_store = config.database_dir / "connectors.sqlite3"
        displaced = config.database_dir / "connectors.sqlite3.unavailable"
        assert required_store.is_file()
        required_store.replace(displaced)
        try:
            unavailable = await deployment.control_plane.health()
            assert unavailable["ready"] is False
            diagnostics = unavailable["providers"][0].get("diagnostics", [])
            persistence = next(
                item for item in diagnostics if item.get("dependency") == "persistence"
            )
            assert persistence["status"] == "unavailable"
            nested = persistence.get("diagnostics", [])
            assert any(item.get("code") == "required_store_missing" for item in nested)
        finally:
            displaced.replace(required_store)

        recovered = await deployment.control_plane.health()
        assert recovered["ready"] is True
        assert deployment.observability_exporter.logs[-1].event_name == "persistence.recovered"

    asyncio.run(scenario())



def test_base_profile_does_not_require_uncomposed_extension_stores(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        config = SingleNodeConfig(data_dir=tmp_path / "base-platform", secure_cookie=False)
        deployment = build_base_single_node_deployment(config)

        assert not (config.database_dir / "connectors.sqlite3").exists()
        assert not (config.database_dir / "learning.sqlite3").exists()

        health = await deployment.control_plane.health()
        assert health["ready"] is True
        diagnostics = health["providers"][0].get("diagnostics", [])
        assert not any(
            nested.get("code") == "required_store_missing"
            for item in diagnostics
            for nested in item.get("diagnostics", [])
        )

    asyncio.run(scenario())
