from __future__ import annotations

import asyncio

import pytest

from ai_multi_agent_platform.capabilities import CapabilityRegistry
from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.control_plane import (
    ActorContext,
    ControlPlane,
    ControlPlaneHTTP,
    HTTPRequest,
    RequestContext,
)
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.plugins import (
    PLUGIN_CONFIGURE_COMMAND,
    PLUGIN_DISABLE_COMMAND,
    PLUGIN_DISCOVER_COMMAND,
    PLUGIN_ENABLE_COMMAND,
    PLUGIN_INSTALL_COMMAND,
    PLUGIN_REMOVE_COMMAND,
    CapabilityRegistryBinder,
    DiscoveredPlugin,
    ExtensionType,
    PluginCatalog,
    PluginPermission,
    PluginRegistry,
    ReferenceCapabilityPlugin,
    StaticPluginSource,
    plugin_command_handlers,
    plugin_resource_services,
    reference_manifest,
)
from ai_multi_agent_platform.plugins.reference import REFERENCE_CAPABILITY_ID
from ai_multi_agent_platform.security import (
    ActorIdentity,
    ActorType,
    AuthorizationAction,
    AuthorizationGate,
    ControlPlaneAuthorizationBridge,
    LocalAuthorizationProvider,
    LocalPrincipalPolicy,
    ResourceType,
    canonical_control_plane_vocabulary,
)
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator


def _kernel() -> tuple[InMemoryKernelRepository, PlatformKernel]:
    repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
    )
    return repository, kernel


def _registry(capabilities: CapabilityRegistry | None = None) -> PluginRegistry:
    binders = {}
    if capabilities is not None:
        binders[ExtensionType.CAPABILITY_PROVIDER] = CapabilityRegistryBinder(capabilities)
    return PluginRegistry(
        platform_version="0.0.1",
        supported_interfaces={ExtensionType.CAPABILITY_PROVIDER: frozenset({"1.0"})},
        binders=binders,
    )


def _catalog() -> PluginCatalog:
    return PluginCatalog(
        StaticPluginSource(
            DiscoveredPlugin(
                manifest=reference_manifest(),
                runtime_factory=ReferenceCapabilityPlugin,
                install_source="bundled:test",
            )
        )
    )


def _context(key: str, *, principal_ref: str = "user:test") -> RequestContext:
    return RequestContext(
        request_id=f"request-{key}",
        correlation_id=f"correlation-{key}",
        actor=ActorContext(
            principal_ref=principal_ref,
            owner_type="user",
            owner_id=principal_ref.removeprefix("user:"),
        ),
        idempotency_key=key,
    )


def _headers(key: str) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "X-Request-Id": f"request-{key}",
        "X-Correlation-Id": f"correlation-{key}",
        "X-Principal-Ref": "user:test",
        "X-Owner-Type": "user",
        "X-Owner-Id": "test",
        "Idempotency-Key": key,
    }


def _allowing_bridge() -> ControlPlaneAuthorizationBridge:
    policy = LocalPrincipalPolicy(
        principal_ref="user:test",
        actor_types=frozenset({ActorType.HUMAN}),
        allowed_actions=frozenset(
            {
                AuthorizationAction.VIEW,
                AuthorizationAction.READ,
                AuthorizationAction.CREATE,
                AuthorizationAction.MODIFY,
                AuthorizationAction.DELETE,
                AuthorizationAction.ADMINISTER,
            }
        ),
        resource_types=frozenset({ResourceType.PLUGIN}),
    )
    return ControlPlaneAuthorizationBridge(AuthorizationGate(LocalAuthorizationProvider((policy,))))


def _stack(
    registry: PluginRegistry,
    catalog: PluginCatalog,
    *,
    authorization: ControlPlaneAuthorizationBridge | None = None,
    permission_grants=None,
) -> tuple[ControlPlane, ControlPlaneHTTP]:
    repository, kernel = _kernel()
    control_plane = ControlPlane(
        kernel=kernel,
        events=repository,
        authorization=authorization,
        resource_services=plugin_resource_services(registry, catalog),
        command_handlers=plugin_command_handlers(
            registry,
            catalog,
            permission_grants=permission_grants,
        ),
    )
    return control_plane, ControlPlaneHTTP(control_plane)


def test_plugin_control_plane_lifecycle_uses_registered_canonical_surfaces() -> None:
    async def scenario() -> None:
        capabilities = CapabilityRegistry()
        registry = _registry(capabilities)
        catalog = _catalog()

        async def grants(context, manifest):
            del context, manifest
            return frozenset({PluginPermission.CAPABILITY_REGISTRATION})

        _, http = _stack(
            registry,
            catalog,
            authorization=_allowing_bridge(),
            permission_grants=grants,
        )

        manifest = await http.handle(HTTPRequest(method="GET", path="/api/v1"))
        assert manifest.status == 200
        assert isinstance(manifest.body, dict)
        assert "plugins" in manifest.body["resources"]
        assert "plugin-candidates" in manifest.body["resources"]
        assert PLUGIN_DISCOVER_COMMAND in manifest.body["commands"]
        assert PLUGIN_ENABLE_COMMAND in manifest.body["commands"]

        discovered = await http.handle(
            HTTPRequest(
                method="POST",
                path=f"/api/v1/commands/{PLUGIN_DISCOVER_COMMAND}",
                headers=_headers("discover"),
                body={"resource_ref": "plugins"},
            )
        )
        assert discovered.status == 200
        assert isinstance(discovered.body, dict)
        assert discovered.body["candidate_ids"] == [reference_manifest().plugin_id]

        candidates = await http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/plugin-candidates",
                headers=_headers("candidate-list"),
            )
        )
        assert candidates.status == 200
        assert isinstance(candidates.body, dict)
        candidate = candidates.body["items"][0]
        assert isinstance(candidate, dict)
        assert candidate["id"] == reference_manifest().plugin_id
        assert candidate["install_source"] == "bundled:test"
        assert candidate["configuration_schema"] == reference_manifest().configuration_schema
        assert "runtime_factory" not in candidate

        installed = await http.handle(
            HTTPRequest(
                method="POST",
                path=f"/api/v1/commands/{PLUGIN_INSTALL_COMMAND}",
                headers=_headers("install"),
                body={"resource_ref": reference_manifest().plugin_id},
            )
        )
        assert installed.status == 200
        assert isinstance(installed.body, dict)
        assert installed.body["state"] == "installed"
        assert installed.body["configuration_schema"] == reference_manifest().configuration_schema

        configured = await http.handle(
            HTTPRequest(
                method="POST",
                path=f"/api/v1/commands/{PLUGIN_CONFIGURE_COMMAND}",
                headers=_headers("configure"),
                body={
                    "resource_ref": reference_manifest().plugin_id,
                    "configuration": {"prefix": "cp:"},
                },
            )
        )
        assert configured.status == 200
        assert isinstance(configured.body, dict)
        assert configured.body["configured"] is True

        enabled = await http.handle(
            HTTPRequest(
                method="POST",
                path=f"/api/v1/commands/{PLUGIN_ENABLE_COMMAND}",
                headers=_headers("enable"),
                body={"resource_ref": reference_manifest().plugin_id},
            )
        )
        assert enabled.status == 200
        assert isinstance(enabled.body, dict)
        assert enabled.body["state"] == "enabled"
        assert enabled.body["granted_permissions"] == ["capability_registration"]
        assert [item.capability_id for item in capabilities.list_capabilities()] == [
            REFERENCE_CAPABILITY_ID
        ]

        disabled = await http.handle(
            HTTPRequest(
                method="POST",
                path=f"/api/v1/commands/{PLUGIN_DISABLE_COMMAND}",
                headers=_headers("disable"),
                body={"resource_ref": reference_manifest().plugin_id},
            )
        )
        assert disabled.status == 200
        assert capabilities.list_capabilities() == ()

        removed = await http.handle(
            HTTPRequest(
                method="POST",
                path=f"/api/v1/commands/{PLUGIN_REMOVE_COMMAND}",
                headers=_headers("remove"),
                body={"resource_ref": reference_manifest().plugin_id},
            )
        )
        assert removed.status == 200
        assert isinstance(removed.body, dict)
        assert removed.body["removed"] is True
        assert registry.list_plugins() == ()

    asyncio.run(scenario())


def test_plugin_enable_permissions_are_server_resolved_not_client_granted() -> None:
    async def scenario() -> None:
        registry = _registry()
        catalog = _catalog()
        catalog.refresh()
        registry.install(reference_manifest())
        _, http = _stack(registry, catalog, authorization=_allowing_bridge())

        injected = await http.handle(
            HTTPRequest(
                method="POST",
                path=f"/api/v1/commands/{PLUGIN_ENABLE_COMMAND}",
                headers=_headers("client-grants"),
                body={
                    "resource_ref": reference_manifest().plugin_id,
                    "granted_permissions": ["capability_registration"],
                },
            )
        )
        assert injected.status == 400
        assert isinstance(injected.body, dict)
        assert injected.body["code"] == "invalid_request"

        denied = await http.handle(
            HTTPRequest(
                method="POST",
                path=f"/api/v1/commands/{PLUGIN_ENABLE_COMMAND}",
                headers=_headers("no-server-grants"),
                body={"resource_ref": reference_manifest().plugin_id},
            )
        )
        assert denied.status == 403
        assert isinstance(denied.body, dict)
        assert denied.body["code"] == "forbidden"
        assert registry.get(reference_manifest().plugin_id).state.value == "installed"

    asyncio.run(scenario())


def test_plugin_command_idempotency_replays_same_action_and_rejects_key_reuse() -> None:
    async def scenario() -> None:
        registry = _registry()
        catalog = _catalog()
        control_plane, _ = _stack(registry, catalog)

        discover_context = _context("same-key")
        first = await control_plane.execute_command(
            discover_context,
            PLUGIN_DISCOVER_COMMAND,
            "plugins",
            {},
        )
        second = await control_plane.execute_command(
            discover_context,
            PLUGIN_DISCOVER_COMMAND,
            "plugins",
            {},
        )
        assert second == first

        with pytest.raises(ContractError) as caught:
            await control_plane.execute_command(
                discover_context,
                PLUGIN_INSTALL_COMMAND,
                reference_manifest().plugin_id,
                {},
            )
        assert caught.value.code is ErrorCode.CONFLICT
        assert registry.list_plugins() == ()

    asyncio.run(scenario())


def test_plugin_configuration_approval_binds_exact_payload() -> None:
    async def scenario() -> None:
        registry = _registry()
        catalog = _catalog()
        catalog.refresh()
        registry.install(reference_manifest())
        provider = LocalAuthorizationProvider(
            (
                LocalPrincipalPolicy(
                    principal_ref="user:test",
                    actor_types=frozenset({ActorType.HUMAN}),
                    allowed_actions=frozenset({AuthorizationAction.READ}),
                    approval_actions=frozenset({AuthorizationAction.MODIFY}),
                    resource_types=frozenset({ResourceType.PLUGIN}),
                ),
                LocalPrincipalPolicy(
                    principal_ref="user:reviewer",
                    actor_types=frozenset({ActorType.HUMAN}),
                    allowed_actions=frozenset({AuthorizationAction.APPROVE}),
                ),
            )
        )
        gate = AuthorizationGate(provider)
        control_plane, _ = _stack(
            registry,
            catalog,
            authorization=ControlPlaneAuthorizationBridge(gate),
        )
        context = _context("approval-config")
        payload_a = {"configuration": {"prefix": "A:"}}
        payload_b = {"configuration": {"prefix": "B:"}}

        with pytest.raises(ContractError) as caught:
            await control_plane.execute_command(
                context,
                PLUGIN_CONFIGURE_COMMAND,
                reference_manifest().plugin_id,
                payload_a,
            )
        assert caught.value.code is ErrorCode.FORBIDDEN
        first = gate.approvals.all()[0]

        await gate.decide_approval(
            first.approval_id,
            approver=ActorIdentity("user:reviewer", ActorType.HUMAN),
            approve=True,
            operation=OperationContext(correlation_id="correlation-plugin-review"),
        )
        configured = await control_plane.execute_command(
            context,
            PLUGIN_CONFIGURE_COMMAND,
            reference_manifest().plugin_id,
            payload_a,
        )
        assert configured["configured"] is True

        with pytest.raises(ContractError) as changed:
            await control_plane.execute_command(
                context,
                PLUGIN_CONFIGURE_COMMAND,
                reference_manifest().plugin_id,
                payload_b,
            )
        assert changed.value.code is ErrorCode.FORBIDDEN
        approvals = gate.approvals.all()
        assert len(approvals) == 2
        assert approvals[1].requested_action_digest != first.requested_action_digest

    asyncio.run(scenario())


def test_plugin_control_plane_vocabulary_is_canonical() -> None:
    assert canonical_control_plane_vocabulary(PLUGIN_INSTALL_COMMAND) == (
        AuthorizationAction.CREATE,
        ResourceType.PLUGIN,
    )
    assert canonical_control_plane_vocabulary(PLUGIN_ENABLE_COMMAND) == (
        AuthorizationAction.ADMINISTER,
        ResourceType.PLUGIN,
    )
    assert canonical_control_plane_vocabulary(PLUGIN_REMOVE_COMMAND) == (
        AuthorizationAction.DELETE,
        ResourceType.PLUGIN,
    )
    assert canonical_control_plane_vocabulary("plugin:list") == (
        AuthorizationAction.VIEW,
        ResourceType.PLUGIN,
    )
    assert canonical_control_plane_vocabulary("plugin-candidate:read") == (
        AuthorizationAction.READ,
        ResourceType.PLUGIN,
    )
