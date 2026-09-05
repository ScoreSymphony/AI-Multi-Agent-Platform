from __future__ import annotations

import asyncio

import pytest

from ai_multi_agent_platform.control_plane.models import ActorContext, RequestContext
from ai_multi_agent_platform.security import SecretReference
from ai_multi_agent_platform.templates.environment import PlatformTemplateEnvironmentResolver
from ai_multi_agent_platform.templates.materialization import MaterializingTemplateEnvironment


def _context() -> RequestContext:
    return RequestContext(
        request_id="issue-78-bindings",
        correlation_id="issue-78-bindings",
        actor=ActorContext(
            principal_ref="user:issue-78-bindings",
            owner_type="user",
            owner_id="user:issue-78-bindings",
            actor_type="human",
        ),
    )


def test_environment_resolver_derives_resolved_ids_from_actual_server_owned_bindings() -> None:
    async def scenario() -> None:
        secret_reference = SecretReference(
            provider="local",
            secret_id="model-key",
            scope="model-provider",
        )
        resolver = PlatformTemplateEnvironmentResolver(
            placeholder_bindings=lambda _: {"agent_name": "Researcher"},
            secret_reference_bindings=lambda _: {"credential": secret_reference},
            configuration_payloads=lambda _: {
                "config://agents/researcher": {"name": "${agent_name}"}
            },
        )

        environment = await resolver.resolve(_context())

        assert isinstance(environment, MaterializingTemplateEnvironment)
        assert environment.resolved_placeholders == frozenset({"agent_name"})
        assert environment.resolved_secret_reference_placeholders == frozenset({"credential"})
        assert environment.validated_configuration_refs == frozenset(
            {"config://agents/researcher"}
        )
        assert environment.placeholder_bindings["agent_name"] == "Researcher"
        assert environment.secret_reference_bindings["credential"] == secret_reference
        assert environment.configuration_payloads["config://agents/researcher"] == {
            "name": "${agent_name}"
        }

    asyncio.run(scenario())


def test_environment_resolver_preserves_legacy_resolved_inventory_but_apply_bindings_stay_empty() -> None:
    async def scenario() -> None:
        resolver = PlatformTemplateEnvironmentResolver(
            placeholders=lambda _: ("legacy-placeholder",),
            validated_configuration_refs=lambda _: ("config://validated-only",),
        )

        environment = await resolver.resolve(_context())

        assert environment.resolved_placeholders == frozenset({"legacy-placeholder"})
        assert environment.validated_configuration_refs == frozenset({"config://validated-only"})
        assert environment.placeholder_bindings == {}
        assert environment.configuration_payloads == {}

    asyncio.run(scenario())


def test_environment_resolver_rejects_noncanonical_secret_binding_values() -> None:
    async def scenario() -> None:
        resolver = PlatformTemplateEnvironmentResolver(
            secret_reference_bindings=lambda _: {"credential": "plaintext-not-a-reference"}  # type: ignore[dict-item]
        )
        with pytest.raises(ValueError, match="canonical SecretReference"):
            await resolver.resolve(_context())

    asyncio.run(scenario())
