from __future__ import annotations

import asyncio

from ai_multi_agent_platform.control_plane.models import ActorContext, RequestContext
from ai_multi_agent_platform.templates.environment import PlatformTemplateEnvironmentResolver


def _context() -> RequestContext:
    return RequestContext(
        request_id="request-issue-78-environment-versions",
        correlation_id="correlation-issue-78-environment-versions",
        actor=ActorContext(
            principal_ref="user:issue-78-environment",
            owner_type="user",
            owner_id="issue-78-environment",
            actor_type="human",
        ),
    )


def test_resolver_exposes_only_server_owned_version_inventories() -> None:
    async def scenario() -> None:
        resolver = PlatformTemplateEnvironmentResolver(
            capabilities=lambda: ("tool.legacy",),
            capability_versions=lambda: (
                ("tool.search", "2.0"),
                ("tool.search", "1.5"),
                ("tool.search", "2.0"),
            ),
            platform_version="2.4.1",
            contract_versions=lambda: {"agent": "2.0", "automation": "1.1"},
        )

        environment = await resolver.resolve(_context())

        assert environment.capability_ids == frozenset({"tool.legacy", "tool.search"})
        assert environment.capability_versions == {"tool.search": ("1.5", "2.0")}
        assert environment.platform_version == "2.4.1"
        assert environment.contract_versions == {"agent": "2.0", "automation": "1.1"}

    asyncio.run(scenario())


def test_unknown_version_and_extension_inventories_remain_fail_closed() -> None:
    async def scenario() -> None:
        environment = await PlatformTemplateEnvironmentResolver().resolve(_context())

        assert environment.capability_ids == frozenset()
        assert environment.capability_versions == {}
        assert environment.platform_version is None
        assert environment.contract_versions == {}
        assert environment.plugin_ids == frozenset()
        assert environment.connector_ids == frozenset()
        assert environment.model_policy_refs == frozenset()
        assert environment.grantable_permissions == frozenset()

    asyncio.run(scenario())
