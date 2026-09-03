from __future__ import annotations

import asyncio
import json

from ai_multi_agent_platform.capabilities.registry import CapabilityRegistry
from ai_multi_agent_platform.contracts.types import AuthorizationDecision, AuthorizationRequest
from ai_multi_agent_platform.control_plane import ControlPlane, ControlPlaneHTTP, HTTPRequest
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.plugins import (
    CapabilityRegistryBinder,
    DiscoveredPlugin,
    ExtensionType,
    PluginCatalog,
    PluginRegistry,
    ReferenceCapabilityPlugin,
    StaticPluginSource,
    reference_manifest,
)
from ai_multi_agent_platform.plugins.reference import (
    REFERENCE_CAPABILITY_ID,
    REFERENCE_EXTENSION_ID,
    REFERENCE_PLUGIN_ID,
)
from ai_multi_agent_platform.testing import (
    FakeAuthorizationProvider,
    FakeLifecycleBackend,
    FakeOrchestrator,
)


class PluginSearchAuthorization(FakeAuthorizationProvider):
    def __init__(self, *, deny_discovery: bool = False) -> None:
        super().__init__()
        self.deny_discovery = deny_discovery

    async def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision:
        self.calls.append(request)
        if self.deny_discovery and request.action in {
            "plugin:list",
            "plugin-candidate:list",
        }:
            return AuthorizationDecision(allowed=False, reason="plugin-discovery-hidden")
        return AuthorizationDecision(allowed=True, reason="plugin-discovery-visible")


def _headers(*, key: str | None = None) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "X-Principal-Ref": "user:plugin-search",
        "X-Owner-Type": "user",
        "X-Owner-Id": "plugin-search",
    }
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


def _stack(
    authorization: PluginSearchAuthorization | None = None,
) -> tuple[ControlPlane, ControlPlaneHTTP]:
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
        authorization=authorization or PluginSearchAuthorization(),
        plugin_registry=registry,
        plugin_catalog=catalog,
        plugin_permission_resolver=grants,
    )
    return control_plane, ControlPlaneHTTP(control_plane)


async def _search(
    http: ControlPlaneHTTP,
    *,
    resource_type: str,
    **query: str,
) -> dict[str, object]:
    response = await http.handle(
        HTTPRequest(
            method="GET",
            path="/api/v1/search",
            headers=_headers(),
            query={"type": resource_type, **query},
        )
    )
    assert response.status == 200, response.body
    assert isinstance(response.body, dict)
    return response.body


def _items(page: dict[str, object]) -> list[dict[str, object]]:
    items = page["items"]
    assert isinstance(items, list)
    assert all(isinstance(item, dict) for item in items)
    return items


async def _install_reference_plugin(http: ControlPlaneHTTP) -> None:
    candidate = await http.handle(
        HTTPRequest(
            method="GET",
            path=f"/api/v1/plugin-candidates/{REFERENCE_PLUGIN_ID}",
            headers=_headers(),
        )
    )
    assert candidate.status == 200, candidate.body
    assert isinstance(candidate.body, dict)
    manifest_digest = candidate.body["manifest_digest"]
    assert isinstance(manifest_digest, str)

    installed = await http.handle(
        HTTPRequest(
            method="POST",
            path="/api/v1/commands/plugin.install",
            headers=_headers(key="issue-45-plugin-install"),
            body={
                "resource_ref": REFERENCE_PLUGIN_ID,
                "manifest_digest": manifest_digest,
            },
        )
    )
    assert installed.status == 200, installed.body
    assert isinstance(installed.body, dict)
    assert installed.body["state"] == "installed"


def test_plugin_candidates_and_installed_plugins_use_registered_global_search() -> None:
    async def scenario() -> None:
        control_plane, http = _stack()

        candidate_exact = await _search(
            http,
            resource_type="plugin-candidate",
            id=REFERENCE_PLUGIN_ID,
        )
        assert candidate_exact["total"] == 1
        candidate = _items(candidate_exact)[0]
        assert candidate["resource_type"] == "plugin-candidate"
        assert candidate["resource_id"] == REFERENCE_PLUGIN_ID
        assert candidate["title"] == "Reference capability plugin"
        assert candidate["summary"] == (
            "Deterministic capability provider used to prove plugin lifecycle semantics."
        )
        assert candidate["version"] == "1.0.0"
        assert candidate["canonical_ref"] == (f"/api/v1/plugin-candidates/{REFERENCE_PLUGIN_ID}")
        assert candidate["provenance"] == {
            "indexed_from": "canonical-control-plane",
            "collection": "plugin-candidates",
        }

        for keyword in (
            "ScoreSymphony",
            "1.0.0",
            REFERENCE_CAPABILITY_ID,
            REFERENCE_EXTENSION_ID,
            "capability_provider",
            "capability_registration",
            "bundled-reference",
        ):
            page = await _search(http, resource_type="plugin-candidate", q=keyword)
            assert page["total"] == 1, (keyword, page)
            assert _items(page)[0]["resource_id"] == REFERENCE_PLUGIN_ID

        for nested_manifest_value in (
            "ai_multi_agent_platform.plugins.reference:ReferenceCapabilityPlugin",
            "https://github.com/ScoreSymphony/AI-Multi-Agent-Platform",
            "capability_id",
            "prefix",
        ):
            page = await _search(
                http,
                resource_type="plugin-candidate",
                q=nested_manifest_value,
            )
            assert page["total"] == 0, (nested_manifest_value, page)

        await _install_reference_plugin(http)

        plugin_exact = await _search(
            http,
            resource_type="plugin",
            id=REFERENCE_PLUGIN_ID,
        )
        assert plugin_exact["total"] == 1
        plugin = _items(plugin_exact)[0]
        assert plugin["resource_type"] == "plugin"
        assert plugin["resource_id"] == REFERENCE_PLUGIN_ID
        assert plugin["title"] == "Reference capability plugin"
        assert plugin["status"] == "installed"
        assert plugin["version"] == "1.0.0"
        assert plugin["canonical_ref"] == f"/api/v1/plugins/{REFERENCE_PLUGIN_ID}"
        assert plugin["provenance"] == {
            "indexed_from": "canonical-control-plane",
            "collection": "plugins",
        }

        for keyword in (
            "ScoreSymphony",
            REFERENCE_CAPABILITY_ID,
            REFERENCE_EXTENSION_ID,
            "capability_provider",
            "capability_registration",
            "bundled-reference",
            "unconfigured",
        ):
            page = await _search(http, resource_type="plugin", q=keyword)
            assert page["total"] == 1, (keyword, page)
            assert _items(page)[0]["resource_id"] == REFERENCE_PLUGIN_ID

        rebuilt = await control_plane.rebuild_search_index()
        assert rebuilt >= 2

    asyncio.run(scenario())


def test_plugin_search_authorization_hides_candidates_plugins_counts_and_exact_ids() -> None:
    async def scenario() -> None:
        authorization = PluginSearchAuthorization(deny_discovery=True)
        _, http = _stack(authorization)
        await _install_reference_plugin(http)

        candidate_exact = await _search(
            http,
            resource_type="plugin-candidate",
            id=REFERENCE_PLUGIN_ID,
        )
        plugin_exact = await _search(
            http,
            resource_type="plugin",
            id=REFERENCE_PLUGIN_ID,
        )
        combined_name = await _search(
            http,
            resource_type="plugin,plugin-candidate",
            q="Reference capability plugin",
        )

        assert candidate_exact["total"] == 0
        assert plugin_exact["total"] == 0
        assert combined_name["total"] == 0
        serialized = json.dumps(
            {
                "candidate": candidate_exact,
                "plugin": plugin_exact,
                "combined": combined_name,
            },
            sort_keys=True,
        )
        assert REFERENCE_PLUGIN_ID not in serialized
        assert "Reference capability plugin" not in serialized

        actions = {call.action for call in authorization.calls}
        assert "plugin-candidate:list" in actions
        assert "plugin:list" in actions

    asyncio.run(scenario())
