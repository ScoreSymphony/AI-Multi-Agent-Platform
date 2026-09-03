from __future__ import annotations

import asyncio

from ai_multi_agent_platform.capabilities import CapabilityRegistry, NativeEchoProvider
from ai_multi_agent_platform.capabilities.control_plane import capability_resource_services
from ai_multi_agent_platform.contracts import AdapterMetadata, HealthStatus
from ai_multi_agent_platform.contracts.types import AuthorizationDecision, AuthorizationRequest
from ai_multi_agent_platform.control_plane import ControlPlane, ControlPlaneHTTP, HTTPRequest
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.models import (
    ModelCapabilities,
    ModelConfiguration,
    ModelLocation,
    ModelRegistry,
)
from ai_multi_agent_platform.testing import (
    FakeAuthorizationProvider,
    FakeLifecycleBackend,
    FakeModelProvider,
    FakeOrchestrator,
)


class InventorySearchAuthorization(FakeAuthorizationProvider):
    def __init__(self, denied_actions: frozenset[str] = frozenset()) -> None:
        super().__init__()
        self.denied_actions = denied_actions

    async def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision:
        self.calls.append(request)
        if request.action in self.denied_actions:
            return AuthorizationDecision(allowed=False, reason="inventory-hidden")
        return AuthorizationDecision(allowed=True, reason="inventory-visible")


async def _stack(
    authorization: FakeAuthorizationProvider | None = None,
) -> tuple[ControlPlane, ControlPlaneHTTP]:
    repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
    )

    model_registry = ModelRegistry()
    model_provider = FakeModelProvider()
    model_registry.register_provider(model_provider)
    model_registry.register_model(
        ModelConfiguration(
            config_id="model-search-local",
            display_name="Search Local General",
            provider_id=model_provider.descriptor.provider_id,
            aliases=("search-general",),
            location=ModelLocation.LOCAL,
            capabilities=ModelCapabilities(
                context_window=32768,
                tool_calling=True,
                structured_output=True,
                streaming=True,
            ),
            health=HealthStatus.HEALTHY,
            adapter_metadata=(
                AdapterMetadata(
                    namespace="provider-private",
                    values={"model": "provider/native-secret-model"},
                ),
            ),
        )
    )

    capability_registry = CapabilityRegistry()
    await capability_registry.register_provider(NativeEchoProvider())

    control_plane = ControlPlane(
        kernel=kernel,
        events=repository,
        authorization=authorization,
        model_registry=model_registry,
        resource_services=capability_resource_services(capability_registry),
    )
    return control_plane, ControlPlaneHTTP(control_plane)


async def _search(http: ControlPlaneHTTP, **query: str) -> dict[str, object]:
    response = await http.handle(
        HTTPRequest(method="GET", path="/api/v1/search", query=query)
    )
    assert response.status == 200
    assert isinstance(response.body, dict)
    return response.body


def _items(page: dict[str, object]) -> list[dict[str, object]]:
    raw_items = page["items"]
    assert isinstance(raw_items, list)
    assert all(isinstance(item, dict) for item in raw_items)
    return raw_items


def test_models_providers_and_registered_capabilities_share_global_search() -> None:
    async def scenario() -> None:
        control_plane, http = await _stack()

        alias_page = await _search(http, q="search-general", type="model")
        assert alias_page["total"] == 1
        model = _items(alias_page)[0]
        assert model["resource_type"] == "model"
        assert model["resource_id"] == "model-search-local"
        assert model["title"] == "Search Local General"
        assert model["status"] == "healthy"
        assert model["version"] == "1"
        assert model["canonical_ref"] == "/api/v1/models/model-search-local"
        assert model["provenance"] == {
            "indexed_from": "canonical-control-plane",
            "resource_provider_id": FakeModelProvider.descriptor.provider_id,
        }

        provider_page = await _search(
            http,
            id=FakeModelProvider.descriptor.provider_id,
            type="model-provider",
        )
        assert provider_page["total"] == 1
        provider = _items(provider_page)[0]
        assert provider["resource_type"] == "model-provider"
        assert provider["status"] == "healthy"
        assert provider["canonical_ref"] == (
            f"/api/v1/model-providers/{FakeModelProvider.descriptor.provider_id}"
        )

        healthy_models = await _search(http, type="model", status="healthy")
        assert healthy_models["total"] == 1

        provider_keyword = await _search(
            http,
            q=FakeModelProvider.descriptor.provider_id,
            type="model",
        )
        assert provider_keyword["total"] == 1

        private_name = await _search(
            http,
            q="provider/native-secret-model",
            type="model",
        )
        assert private_name["total"] == 0
        assert "provider/native-secret-model" not in repr(private_name)

        capability_page = await _search(http, id="tool.echo", type="capability")
        assert capability_page["total"] == 1
        capability = _items(capability_page)[0]
        assert capability["resource_type"] == "capability"
        assert capability["resource_id"] == "tool.echo"
        assert capability["status"] == "available"
        assert capability["canonical_ref"] == "/api/v1/capabilities/tool.echo"
        assert capability["provenance"] == {
            "indexed_from": "canonical-control-plane",
            "collection": "capabilities",
        }

        rebuilt = await control_plane.rebuild_search_index()
        assert rebuilt == 4

    asyncio.run(scenario())


def test_inventory_authorization_removes_counts_and_exact_lookup_disclosure() -> None:
    async def scenario() -> None:
        authorization = InventorySearchAuthorization(
            frozenset({"model-provider:list", "capability:list"})
        )
        _, http = await _stack(authorization)

        hidden = await _search(http, type="model-provider,capability")
        assert hidden["total"] == 0
        assert _items(hidden) == []
        serialized = repr(hidden)
        assert FakeModelProvider.descriptor.provider_id not in serialized
        assert "tool.echo" not in serialized

        hidden_provider = await _search(
            http,
            id=FakeModelProvider.descriptor.provider_id,
            type="model-provider",
        )
        assert hidden_provider["total"] == 0
        assert FakeModelProvider.descriptor.provider_id not in repr(hidden_provider)

        hidden_capability = await _search(http, id="tool.echo", type="capability")
        assert hidden_capability["total"] == 0
        assert "tool.echo" not in repr(hidden_capability)

        visible_model = await _search(http, id="model-search-local", type="model")
        assert visible_model["total"] == 1

        assert any(call.action == "model-provider:list" for call in authorization.calls)
        assert any(call.action == "capability:list" for call in authorization.calls)
        assert any(call.action == "model:list" for call in authorization.calls)

    asyncio.run(scenario())
