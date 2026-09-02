from __future__ import annotations

import asyncio

from ai_multi_agent_platform.contracts import (
    AdapterMetadata,
    Capability,
    CapabilityKind,
    HealthStatus,
    ProviderDescriptor,
)
from ai_multi_agent_platform.control_plane import (
    ControlPlane,
    ControlPlaneHTTP,
    HTTPRequest,
    build_openapi,
)
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.models import (
    ModelCapabilities,
    ModelConfiguration,
    ModelLocation,
    ModelRegistry,
)
from ai_multi_agent_platform.testing import (
    FakeLifecycleBackend,
    FakeModelProvider,
    FakeOrchestrator,
)


class LocalModelProvider(FakeModelProvider):
    descriptor = ProviderDescriptor(
        provider_id="local-openai-compatible",
        provider_type="model",
        supported_operations=("generate",),
        capabilities=(
            Capability(
                name="model.text",
                kind=CapabilityKind.MODEL,
                supported_operations=("generate",),
                modalities=("text",),
            ),
        ),
        health=HealthStatus.HEALTHY,
        available=True,
    )


def _http() -> ControlPlaneHTTP:
    repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
    )
    registry = ModelRegistry()
    registry.register_provider(LocalModelProvider())
    registry.register_model(
        ModelConfiguration(
            config_id="model-local-general",
            display_name="Local General",
            provider_id="local-openai-compatible",
            aliases=("general",),
            location=ModelLocation.LOCAL,
            capabilities=ModelCapabilities(
                context_window=32768,
                tool_calling=True,
                structured_output=True,
                streaming=True,
                modalities=("text",),
            ),
            health=HealthStatus.HEALTHY,
            priority=50,
            adapter_metadata=(
                AdapterMetadata(
                    namespace="openai-compatible",
                    values={"model": "local/native-model"},
                ),
            ),
        )
    )
    control_plane = ControlPlane(
        kernel=kernel,
        events=repository,
        model_registry=registry,
    )
    return ControlPlaneHTTP(control_plane)


def _headers(key: str | None = None) -> dict[str, str]:
    headers = {
        "x-principal-ref": "user:test",
        "x-owner-type": "user",
        "x-owner-id": "test",
    }
    if key is not None:
        headers["idempotency-key"] = key
    return headers


def test_model_and_provider_inventory_is_exposed_through_control_plane() -> None:
    async def scenario() -> None:
        http = _http()
        providers = await http.handle(
            HTTPRequest(method="GET", path="/api/v1/model-providers", headers=_headers())
        )
        assert providers.status == 200
        assert isinstance(providers.body, dict)
        provider_items = providers.body["items"]
        assert isinstance(provider_items, list)
        assert provider_items[0]["id"] == "local-openai-compatible"
        assert provider_items[0]["enabled"] is True
        assert provider_items[0]["health"] == "healthy"

        models = await http.handle(
            HTTPRequest(method="GET", path="/api/v1/models", headers=_headers())
        )
        assert models.status == 200
        assert isinstance(models.body, dict)
        model_items = models.body["items"]
        assert isinstance(model_items, list)
        assert model_items[0]["id"] == "model-local-general"
        assert model_items[0]["provider_id"] == "local-openai-compatible"
        assert model_items[0]["effective_health"] == "healthy"

        alias = await http.handle(
            HTTPRequest(method="GET", path="/api/v1/models/general", headers=_headers())
        )
        assert alias.status == 200
        assert isinstance(alias.body, dict)
        assert alias.body["id"] == "model-local-general"

    asyncio.run(scenario())


def test_control_plane_can_disable_and_reenable_model_and_provider_inventory() -> None:
    async def scenario() -> None:
        http = _http()
        disabled_model = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/models/general:disable",
                headers=_headers("disable-model"),
            )
        )
        assert disabled_model.status == 200
        assert isinstance(disabled_model.body, dict)
        assert disabled_model.body["enabled"] is False
        assert disabled_model.body["revision"] == 2

        enabled_model = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/models/model-local-general:enable",
                headers=_headers("enable-model"),
            )
        )
        assert enabled_model.status == 200
        assert isinstance(enabled_model.body, dict)
        assert enabled_model.body["enabled"] is True
        assert enabled_model.body["revision"] == 3

        disabled_provider = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/model-providers/local-openai-compatible:disable",
                headers=_headers("disable-provider"),
            )
        )
        assert disabled_provider.status == 200
        assert isinstance(disabled_provider.body, dict)
        assert disabled_provider.body["enabled"] is False
        assert disabled_provider.body["health"] == "unavailable"

        model = await http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/models/model-local-general",
                headers=_headers(),
            )
        )
        assert model.status == 200
        assert isinstance(model.body, dict)
        assert model.body["effective_health"] == "unavailable"

        enabled_provider = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/model-providers/local-openai-compatible:enable",
                headers=_headers("enable-provider"),
            )
        )
        assert enabled_provider.status == 200
        refreshed = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/model-providers/local-openai-compatible:refresh-health",
                headers=_headers("refresh-provider"),
            )
        )
        assert refreshed.status == 200
        assert isinstance(refreshed.body, dict)
        assert refreshed.body["health"] == "healthy"

    asyncio.run(scenario())


def test_model_inventory_paths_are_declared_in_manifest_and_openapi() -> None:
    async def scenario() -> None:
        http = _http()
        manifest = await http.handle(HTTPRequest(method="GET", path="/api/v1"))
        assert manifest.status == 200
        assert isinstance(manifest.body, dict)
        resources = manifest.body["resources"]
        assert isinstance(resources, list)
        assert "models" in resources
        assert "model-providers" in resources

    asyncio.run(scenario())

    paths = build_openapi()["paths"]
    assert "/api/v1/models" in paths
    assert "/api/v1/models/{model_id}" in paths
    assert "/api/v1/models/{model_id}:enable" in paths
    assert "/api/v1/models/{model_id}:disable" in paths
    assert "/api/v1/model-providers" in paths
    assert "/api/v1/model-providers/{provider_id}" in paths
    assert "/api/v1/model-providers/{provider_id}:refresh-health" in paths
