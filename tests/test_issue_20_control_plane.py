from __future__ import annotations

import asyncio

from ai_multi_agent_platform.capabilities.registry import CapabilityRegistry
from ai_multi_agent_platform.control_plane import (
    PLUGIN_CANDIDATE_COLLECTION,
    PLUGIN_COLLECTION,
    PLUGIN_COMMANDS,
    ControlPlane,
    ControlPlaneHTTP,
    HTTPRequest,
)
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.plugins import (
    CapabilityRegistryBinder,
    DiscoveredPlugin,
    ExtensionType,
    PluginCatalog,
    PluginPermission,
    PluginRegistry,
    ReferenceCapabilityPlugin,
    StaticPluginSource,
    reference_manifest,
)
from ai_multi_agent_platform.plugins.reference import REFERENCE_CAPABILITY_ID
from ai_multi_agent_platform.security.authorization import AuthorizationAction, ResourceType
from ai_multi_agent_platform.security.control_plane_bridge import canonical_control_plane_vocabulary
from ai_multi_agent_platform.testing import (
    FakeAuthorizationProvider,
    FakeLifecycleBackend,
    FakeOrchestrator,
)


def _headers(*, key: str | None = None) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "X-Request-Id": "request-plugin",
        "X-Correlation-Id": "correlation-plugin",
        "X-Principal-Ref": "user:plugin-admin",
        "X-Owner-Type": "user",
        "X-Owner-Id": "plugin-admin",
    }
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


def _stack(*, permission_resolver: bool = True):
    repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
    )
    capabilities = CapabilityRegistry()
    registry = PluginRegistry(
        platform_version="0.0.1",
        supported_interfaces={ExtensionType.CAPABILITY_PROVIDER: frozenset({"1.0"})},
        binders={
            ExtensionType.CAPABILITY_PROVIDER: CapabilityRegistryBinder(capabilities),
        },
    )
    manifest = reference_manifest()
    catalog = PluginCatalog(
        StaticPluginSource(
            DiscoveredPlugin(
                manifest=manifest,
                runtime_factory=ReferenceCapabilityPlugin,
                install_source="bundled-reference",
            )
        )
    )

    async def grants(context, candidate_manifest):
        del context
        return candidate_manifest.requested_permissions

    control_plane = ControlPlane(
        kernel=kernel,
        events=repository,
        authorization=FakeAuthorizationProvider(),
        plugin_registry=registry,
        plugin_catalog=catalog,
        plugin_permission_resolver=grants if permission_resolver else None,
    )
    return control_plane, ControlPlaneHTTP(control_plane), registry, capabilities


def _item(response) -> dict[str, object]:
    assert response.status == 200
    assert isinstance(response.body, dict)
    items = response.body["items"]
    assert isinstance(items, list)
    assert len(items) == 1
    item = items[0]
    assert isinstance(item, dict)
    return item


def test_plugin_surface_is_registered_only_when_plugin_runtime_is_composed() -> None:
    async def scenario() -> None:
        control_plane, http, _, _ = _stack()
        assert PLUGIN_COLLECTION in control_plane.registered_collections
        assert PLUGIN_CANDIDATE_COLLECTION in control_plane.registered_collections
        assert set(PLUGIN_COMMANDS).issubset(control_plane.registered_commands)

        openapi = await http.handle(
            HTTPRequest(method="GET", path="/api/v1/openapi.json", headers=_headers())
        )
        assert openapi.status == 200
        assert isinstance(openapi.body, dict)
        paths = openapi.body["paths"]
        assert isinstance(paths, dict)
        assert f"/api/v1/{PLUGIN_COLLECTION}" in paths
        assert f"/api/v1/{PLUGIN_CANDIDATE_COLLECTION}" in paths
        assert set(PLUGIN_COMMANDS).issubset(openapi.body["x-registered-extension-commands"])

    asyncio.run(scenario())


def test_reference_plugin_lifecycle_runs_through_control_plane_only() -> None:
    async def scenario() -> None:
        _, http, registry, capabilities = _stack()
        plugin_id = reference_manifest().plugin_id

        candidates = await http.handle(
            HTTPRequest(
                method="GET",
                path=f"/api/v1/{PLUGIN_CANDIDATE_COLLECTION}",
                headers=_headers(),
            )
        )
        candidate = _item(candidates)
        manifest_digest = candidate["manifest_digest"]
        assert isinstance(manifest_digest, str)
        assert candidate["requested_permissions"] == [PluginPermission.CAPABILITY_REGISTRATION.value]

        installed = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/plugin.install",
                headers=_headers(key="plugin-install"),
                body={
                    "resource_ref": plugin_id,
                    "manifest_digest": manifest_digest,
                },
            )
        )
        assert installed.status == 200
        assert isinstance(installed.body, dict)
        assert installed.body["state"] == "installed"
        assert installed.body["granted_permissions"] == []

        configured = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/plugin.configure",
                headers=_headers(key="plugin-configure"),
                body={
                    "resource_ref": plugin_id,
                    "configuration": {"prefix": "cp:"},
                },
            )
        )
        assert configured.status == 200
        assert isinstance(configured.body, dict)
        assert configured.body["state"] == "configured"
        assert "configuration" not in configured.body

        enabled = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/plugin.enable",
                headers=_headers(key="plugin-enable"),
                body={
                    "resource_ref": plugin_id,
                    "manifest_digest": manifest_digest,
                },
            )
        )
        assert enabled.status == 200
        assert isinstance(enabled.body, dict)
        assert enabled.body["state"] == "enabled"
        assert enabled.body["granted_permissions"] == [
            PluginPermission.CAPABILITY_REGISTRATION.value
        ]
        assert registry.extension_owner(reference_manifest().extensions[0].extension_id) == plugin_id
        assert [item.capability_id for item in capabilities.list_capabilities()] == [
            REFERENCE_CAPABILITY_ID
        ]

        disabled = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/plugin.disable",
                headers=_headers(key="plugin-disable"),
                body={"resource_ref": plugin_id},
            )
        )
        assert disabled.status == 200
        assert isinstance(disabled.body, dict)
        assert disabled.body["state"] == "disabled"
        assert capabilities.list_capabilities() == ()

        removed = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/plugin.remove",
                headers=_headers(key="plugin-remove"),
                body={"resource_ref": plugin_id},
            )
        )
        assert removed.status == 200
        assert removed.body == {
            "id": plugin_id,
            "type": "plugin-removal",
            "removed": True,
            "plugin_version": "1.0.0",
        }
        assert registry.list_plugins() == ()

    asyncio.run(scenario())


def test_install_refuses_stale_or_uninspected_manifest_digest() -> None:
    async def scenario() -> None:
        _, http, registry, _ = _stack()
        response = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/plugin.install",
                headers=_headers(key="plugin-install-stale"),
                body={
                    "resource_ref": reference_manifest().plugin_id,
                    "manifest_digest": "0" * 64,
                },
            )
        )
        assert response.status == 409
        assert isinstance(response.body, dict)
        assert response.body["code"] == "conflict"
        assert registry.list_plugins() == ()

    asyncio.run(scenario())


def test_enable_requires_authoritative_permission_resolution() -> None:
    async def scenario() -> None:
        _, http, registry, _ = _stack(permission_resolver=False)
        plugin_id = reference_manifest().plugin_id
        candidates = await http.handle(
            HTTPRequest(
                method="GET",
                path=f"/api/v1/{PLUGIN_CANDIDATE_COLLECTION}",
                headers=_headers(),
            )
        )
        candidate = _item(candidates)
        digest = candidate["manifest_digest"]
        assert isinstance(digest, str)

        registry.install(reference_manifest(), install_source="bundled-reference")
        response = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/plugin.enable",
                headers=_headers(key="plugin-enable-no-grants"),
                body={"resource_ref": plugin_id, "manifest_digest": digest},
            )
        )
        assert response.status == 503
        assert isinstance(response.body, dict)
        assert response.body["code"] == "unavailable"
        assert registry.get(plugin_id).granted_permissions == ()

    asyncio.run(scenario())


def test_plugin_actions_map_to_plugin_authorization_resource() -> None:
    expected = {
        "plugin.install": AuthorizationAction.CREATE,
        "plugin.configure": AuthorizationAction.MODIFY,
        "plugin.enable": AuthorizationAction.ADMINISTER,
        "plugin.disable": AuthorizationAction.ADMINISTER,
        "plugin.refresh-health": AuthorizationAction.ADMINISTER,
        "plugin.validate-update": AuthorizationAction.READ,
        "plugin.remove": AuthorizationAction.DELETE,
        "plugin:list": AuthorizationAction.VIEW,
        "plugin-candidate:read": AuthorizationAction.READ,
    }
    for action, canonical_action in expected.items():
        mapped_action, resource_type = canonical_control_plane_vocabulary(action)
        assert mapped_action is canonical_action
        assert resource_type is ResourceType.PLUGIN
