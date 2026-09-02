from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text()
    if old not in text:
        raise SystemExit(f"anchor not found in {path}: {old[:100]!r}")
    target.write_text(text.replace(old, new, 1))


# The full #32 Control Plane is the exported northbound surface. Integrate the
# model registry into its existing /models and /providers collections rather
# than creating a second parallel /model-providers namespace.
extensions = "src/ai_multi_agent_platform/control_plane/extensions.py"
replace_once(
    extensions,
    "from ai_multi_agent_platform.kernel.repository import EventRepository\n",
    "from ai_multi_agent_platform.kernel.repository import EventRepository\nfrom ai_multi_agent_platform.models import ModelRegistry\n",
)
replace_once(
    extensions,
    "        command_handlers: Mapping[str, CommandHandler] | None = None,\n    ) -> None:\n",
    "        command_handlers: Mapping[str, CommandHandler] | None = None,\n        model_registry: ModelRegistry | None = None,\n    ) -> None:\n",
)
replace_once(
    extensions,
    "            health_providers=health_providers,\n        )\n",
    "            health_providers=health_providers,\n            model_registry=model_registry,\n        )\n",
)
replace_once(
    extensions,
    '''        if collection in {"providers", "adapters", "plugins"}:
            resources = await self._provider_inventory(collection)
            return paginate(resources, query)
''',
    '''        if collection == "models":
            return await self.list_models(context, query)
        if collection in {"providers", "adapters", "plugins"}:
            resources = await self._provider_inventory(collection)
            return paginate(resources, query)
''',
)
replace_once(
    extensions,
    '''        if collection in {"providers", "adapters", "plugins", "capabilities"}:
            page = await self.list_extension_resources(context, collection, PageQuery(limit=200))
''',
    '''        if collection == "models":
            return await self.get_model(context, resource_id)
        if collection in {"providers", "adapters", "plugins", "capabilities"}:
            page = await self.list_extension_resources(context, collection, PageQuery(limit=200))
''',
)
old_provider_inventory = '''    async def _provider_inventory(self, collection: str) -> list[dict[str, JsonValue]]:
        resources: list[dict[str, JsonValue]] = []
        for provider in self._health_providers:
            descriptor = provider.descriptor
            health = await provider.health()
            resources.append(
                {
                    "id": descriptor.provider_id,
                    "type": _singular(collection),
                    "provider_type": descriptor.provider_type,
                    "contract_version": descriptor.contract_version,
                    "supported_operations": list(descriptor.supported_operations),
                    "health": health.value,
                    "available": descriptor.available,
                }
            )
        return resources
'''
new_provider_inventory = '''    async def _provider_inventory(self, collection: str) -> list[dict[str, JsonValue]]:
        resources: list[dict[str, JsonValue]] = []
        providers = {
            provider.descriptor.provider_id: provider for provider in self._health_providers
        }
        model_provider_ids: set[str] = set()
        if collection == "providers":
            for provider in self._model_registry.list_providers():
                provider_id = provider.descriptor.provider_id
                providers[provider_id] = provider
                model_provider_ids.add(provider_id)

        for provider_id in sorted(providers):
            provider = providers[provider_id]
            descriptor = provider.descriptor
            if provider_id in model_provider_ids:
                health = self._model_registry.provider_health(provider_id)
            else:
                health = await provider.health()
            resource: dict[str, JsonValue] = {
                "id": descriptor.provider_id,
                "type": _singular(collection),
                "provider_type": descriptor.provider_type,
                "contract_version": descriptor.contract_version,
                "supported_operations": list(descriptor.supported_operations),
                "health": health.value,
                "available": descriptor.available,
            }
            if provider_id in model_provider_ids:
                resource["enabled"] = self._model_registry.provider_enabled(provider_id)
            resources.append(resource)
        return resources
'''
replace_once(extensions, old_provider_inventory, new_provider_inventory)

# Remove the temporary parallel HTTP namespace inserted by the first completion
# pass. The exported full Control Plane already owns /models and /providers.
http = Path("src/ai_multi_agent_platform/control_plane/http.py")
http_text = http.read_text()
http_text = http_text.replace(
    '                            "timeline",\n                            "model-providers",\n                            "models",\n',
    '                            "timeline",\n',
    1,
)
http_text = http_text.replace(
    '''            if segments[0] == "model-providers":
                return await self._model_providers(
                    request, context, query, segments, request_id, correlation_id
                )
            if segments[0] == "models":
                return await self._models(
                    request, context, query, segments, request_id, correlation_id
                )
''',
    "",
    1,
)
method_start = http_text.find("    async def _model_providers(\n")
method_end = http_text.find("    async def _references(\n", method_start)
if method_start < 0 or method_end < 0:
    raise SystemExit("temporary model HTTP methods not found")
http.write_text(http_text[:method_start] + http_text[method_end:])

openapi = Path("src/ai_multi_agent_platform/control_plane/openapi.py")
openapi_text = openapi.read_text()
block_start = openapi_text.find(
    '    paths.update(\n        {\n            f"/api/{API_VERSION}/model-providers"'
)
block_end = openapi_text.find(
    '    for command in ("queue", "start", "cancel", "retry"):\n', block_start
)
if block_start < 0 or block_end < 0:
    raise SystemExit("temporary model OpenAPI block not found")
openapi.write_text(openapi_text[:block_start] + openapi_text[block_end:])

# Keep the full-scope OpenAPI model/provider paths generated by extensions.py.
# No provider-private endpoint is introduced.

tests = Path("tests/test_issue_10_control_plane_inventory.py")
tests.write_text('''from __future__ import annotations

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


def _stack() -> tuple[ControlPlaneHTTP, ModelRegistry]:
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
    return ControlPlaneHTTP(control_plane), registry


def _headers() -> dict[str, str]:
    return {
        "x-principal-ref": "user:test",
        "x-owner-type": "user",
        "x-owner-id": "test",
    }


def test_model_and_provider_inventory_is_exposed_through_canonical_collections() -> None:
    async def scenario() -> None:
        http, _ = _stack()
        providers = await http.handle(
            HTTPRequest(method="GET", path="/api/v1/providers", headers=_headers())
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


def test_inventory_reflects_registry_enable_state_without_parallel_http_mutations() -> None:
    async def scenario() -> None:
        http, registry = _stack()
        registry.set_enabled("general", False)
        registry.set_provider_enabled("local-openai-compatible", False)

        models = await http.handle(
            HTTPRequest(method="GET", path="/api/v1/models/general", headers=_headers())
        )
        assert models.status == 200
        assert isinstance(models.body, dict)
        assert models.body["enabled"] is False
        assert models.body["effective_health"] == "unavailable"

        providers = await http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/providers/local-openai-compatible",
                headers=_headers(),
            )
        )
        assert providers.status == 200
        assert isinstance(providers.body, dict)
        assert providers.body["enabled"] is False
        assert providers.body["health"] == "unavailable"

    asyncio.run(scenario())


def test_model_inventory_uses_existing_full_scope_manifest_and_openapi() -> None:
    async def scenario() -> None:
        http, _ = _stack()
        manifest = await http.handle(HTTPRequest(method="GET", path="/api/v1"))
        assert manifest.status == 200
        assert isinstance(manifest.body, dict)
        resources = manifest.body["resources"]
        assert isinstance(resources, list)
        assert "models" in resources
        assert "providers" in resources
        assert "model-providers" not in resources

    asyncio.run(scenario())

    paths = build_openapi()["paths"]
    assert "/api/v1/models" in paths
    assert "/api/v1/models/{resource_id}" in paths
    assert "/api/v1/providers" in paths
    assert "/api/v1/providers/{resource_id}" in paths
    assert "/api/v1/model-providers" not in paths
''')

# Bring two stale #32-era assertions in the shared Control Plane suite in line
# with the now-full Control Plane and richer run event stream.
control_tests = "tests/test_control_plane.py"
replace_once(
    control_tests,
    '            assert_page(run_events.body, total=1)\n',
    '''            run_items = assert_page(run_events.body)
            assert run_items
            assert all(
                isinstance(item, dict) and item.get("subject_id") == run_id
                for item in run_items
            )
''',
)
replace_once(
    control_tests,
    '''    for future_resource in (
        "agents",
        "models",
        "tools",
        "nodes",
        "automations",
        "evaluations",
        "plugins",
    ):
        assert f"/api/v1/{future_resource}" not in paths
''',
    '''    for platform_resource in (
        "agents",
        "models",
        "tools",
        "nodes",
        "automations",
        "evaluations",
        "plugins",
    ):
        assert f"/api/v1/{platform_resource}" in paths
''',
)

# A structured-output request must receive valid JSON from the reference
# transport; this keeps the runtime test aligned with the provider contract.
runtime_test = "tests/test_issue_10_openai_provider_runtime.py"
replace_once(
    runtime_test,
    '                        "message": {"role": "assistant", "content": "local answer"},\n',
    '                        "message": {"role": "assistant", "content": \'{"answer":"local answer"}\'},\n',
)
replace_once(
    runtime_test,
    '    assert response.text == "local answer"\n',
    '    assert response.text == \'{"answer":"local answer"}\'\n',
)

# Rewrite the completion documentation around the canonical full-scope routes.
docs = Path("docs/MODELS.md")
doc_text = docs.read_text()
marker = "## Control Plane inventory\n"
if marker not in doc_text:
    raise SystemExit("Control Plane inventory documentation marker not found")
prefix = doc_text.split(marker, 1)[0]
docs.write_text(prefix + '''## Control Plane inventory

The full versioned Control Plane exposes model state through its existing canonical
collections rather than through provider-private or duplicate namespaces:

- `GET /api/v1/models`
- `GET /api/v1/models/{model_id_or_alias}`
- `GET /api/v1/providers`
- `GET /api/v1/providers/{provider_id}`

The `/models` collection is backed by `ModelRegistry`. Registered model providers are
included in the canonical `/providers` inventory, including effective registry enable
state and health. Registry mutation remains a platform service/configuration concern;
#10 does not add a second `model-providers` HTTP resource family.

## Issue #10 completion state

The #10 baseline includes distinct provider, registry and router responsibilities;
stable canonical model configuration IDs; persistent reference storage; deterministic
capability/location/health routing; rich canonical request/response types; local
OpenAI-compatible execution; timeout/cancellation/error normalization; configuration
examples; canonical model/provider Control Plane inventory; and end-to-end/contract
coverage.

The baseline remains local-first and requires no recurring paid AI/API service.
Optional gateways and additional commercial or local providers remain replaceable
follow-up adapters.
''')
