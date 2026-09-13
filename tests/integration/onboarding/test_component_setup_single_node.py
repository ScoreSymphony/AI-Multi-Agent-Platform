from __future__ import annotations

import asyncio
from pathlib import Path

from ai_multi_agent_platform.adapters.single_node_app import build_default_single_node_deployment
from ai_multi_agent_platform.control_plane import ActorContext, PageQuery, RequestContext
from ai_multi_agent_platform.deployment.config import SingleNodeConfig
from ai_multi_agent_platform.onboarding import (
    COMPONENT_SETUP_COLLECTION,
    COMPONENT_SETUP_RESOURCE_ID,
    ONBOARDING_SAVE_COMPONENT_PROFILE_COMMAND,
)


def _context(user_id: str, idempotency_key: str) -> RequestContext:
    return RequestContext(
        request_id=f"request-{idempotency_key}",
        correlation_id=f"correlation-{idempotency_key}",
        idempotency_key=idempotency_key,
        actor=ActorContext(
            principal_ref=user_id,
            owner_type="user",
            owner_id=user_id,
            actor_type="human",
        ),
    )


def test_shipped_single_node_registers_component_setup_resource_and_profile_commands(
    tmp_path: Path,
) -> None:
    deployment = build_default_single_node_deployment(
        SingleNodeConfig(data_dir=tmp_path / "single-node")
    )
    admin = deployment.bootstrap_admin("admin", "issue-799-test-password")
    context = _context(admin.user_id, "issue-799-list")

    page = asyncio.run(
        deployment.control_plane.list_extension_resources(
            context,
            COMPONENT_SETUP_COLLECTION,
            PageQuery(),
        )
    )

    items = page["items"]
    assert isinstance(items, list)
    assert len(items) == 1
    setup = items[0]
    assert isinstance(setup, dict)
    assert setup["id"] == COMPONENT_SETUP_RESOURCE_ID
    components = setup["components"]
    assert isinstance(components, list)
    component_ids = {item["component_id"] for item in components if isinstance(item, dict)}
    assert {
        "reference-orchestrator",
        "reference-executor",
        "native-capabilities",
        "local-files",
        "local-node",
    }.issubset(component_ids)
    assert "component-setup" in deployment.control_plane.registered_collections
    assert ONBOARDING_SAVE_COMPONENT_PROFILE_COMMAND in deployment.control_plane.registered_commands


def test_shipped_single_node_can_save_and_restore_local_component_profile(tmp_path: Path) -> None:
    config = SingleNodeConfig(data_dir=tmp_path / "single-node")
    deployment = build_default_single_node_deployment(config)
    admin = deployment.bootstrap_admin("admin", "issue-799-test-password")

    saved = asyncio.run(
        deployment.control_plane.execute_command(
            _context(admin.user_id, "issue-799-save"),
            ONBOARDING_SAVE_COMPONENT_PROFILE_COMMAND,
            COMPONENT_SETUP_RESOURCE_ID,
            {"profile_id": "local", "mode": "local"},
        )
    )

    assert saved["active"] is True
    assert saved["mode"] == "local"
    assert saved["defaults"]["executor"] == "reference-executor"
    assert saved["defaults"]["storage"] == "local-files"
    assert saved["defaults"]["compute"] == "local-node"
    profile_path = config.database_dir / "component-setup-profiles.json"
    serialized = profile_path.read_text(encoding="utf-8").casefold()
    assert "password" not in serialized
    assert "api_key" not in serialized
    assert "authorization" not in serialized

    restarted = build_default_single_node_deployment(config)
    restarted_admin = restarted.bootstrap_admin("admin", "issue-799-test-password")
    page = asyncio.run(
        restarted.control_plane.list_extension_resources(
            _context(restarted_admin.user_id, "issue-799-restart-list"),
            COMPONENT_SETUP_COLLECTION,
            PageQuery(),
        )
    )

    items = page["items"]
    assert isinstance(items, list)
    setup = items[0]
    assert isinstance(setup, dict)
    assert setup["active_profile_id"] == "local"
