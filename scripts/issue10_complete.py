from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text()
    if old not in text:
        raise SystemExit(f"anchor not found in {path}: {old[:80]!r}")
    p.write_text(text.replace(old, new, 1))


service = "src/ai_multi_agent_platform/control_plane/service.py"
replace_once(
    service,
    "from ai_multi_agent_platform.contracts.interfaces import (\n    AuthorizationProvider,\n    EventProvider,\n    ProviderContract,\n)",
    "from ai_multi_agent_platform.contracts.interfaces import (\n    AuthorizationProvider,\n    EventProvider,\n    ModelProvider,\n    ProviderContract,\n)",
)
replace_once(
    service,
    "from ai_multi_agent_platform.kernel.repository import EventRepository\n",
    "from ai_multi_agent_platform.kernel.repository import EventRepository\nfrom ai_multi_agent_platform.models import ModelConfiguration, ModelRegistry\n",
)
replace_once(
    service,
    "        health_providers: tuple[ProviderContract, ...] = (),\n    ) -> None:\n",
    "        health_providers: tuple[ProviderContract, ...] = (),\n        model_registry: ModelRegistry | None = None,\n    ) -> None:\n",
)
replace_once(
    service,
    "        self._health_providers = health_providers\n",
    "        self._health_providers = health_providers\n        self._model_registry = model_registry or ModelRegistry()\n",
)
model_methods = '''    async def list_model_providers(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> dict[str, JsonValue]:
        await self._authorize(context, "model-provider:list", "model-providers")
        resources = [
            _model_provider_resource(self._model_registry, provider)
            for provider in self._model_registry.list_providers()
        ]
        return paginate(resources, query)

    async def get_model_provider(
        self,
        context: RequestContext,
        provider_id: str,
    ) -> dict[str, JsonValue]:
        await self._authorize(context, "model-provider:read", provider_id)
        provider = self._model_registry.get_provider(provider_id)
        return _model_provider_resource(self._model_registry, provider)

    async def set_model_provider_enabled(
        self,
        context: RequestContext,
        provider_id: str,
        *,
        enabled: bool,
    ) -> dict[str, JsonValue]:
        _require_key(context)
        action = "enable" if enabled else "disable"
        await self._authorize(context, f"model-provider:{action}", provider_id)
        self._model_registry.set_provider_enabled(provider_id, enabled)
        provider = self._model_registry.get_provider(provider_id)
        return _model_provider_resource(self._model_registry, provider)

    async def refresh_model_provider_health(
        self,
        context: RequestContext,
        provider_id: str,
    ) -> dict[str, JsonValue]:
        _require_key(context)
        await self._authorize(context, "model-provider:refresh-health", provider_id)
        await self._model_registry.refresh_health(provider_id)
        provider = self._model_registry.get_provider(provider_id)
        return _model_provider_resource(self._model_registry, provider)

    async def list_models(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> dict[str, JsonValue]:
        await self._authorize(context, "model:list", "models")
        resources = [
            _model_resource(self._model_registry, config)
            for config in self._model_registry.list_models()
        ]
        return paginate(resources, query)

    async def get_model(
        self,
        context: RequestContext,
        model_id_or_alias: str,
    ) -> dict[str, JsonValue]:
        await self._authorize(context, "model:read", model_id_or_alias)
        config = self._model_registry.get_model(model_id_or_alias)
        return _model_resource(self._model_registry, config)

    async def set_model_enabled(
        self,
        context: RequestContext,
        model_id_or_alias: str,
        *,
        enabled: bool,
    ) -> dict[str, JsonValue]:
        _require_key(context)
        action = "enable" if enabled else "disable"
        await self._authorize(context, f"model:{action}", model_id_or_alias)
        config = self._model_registry.set_enabled(model_id_or_alias, enabled)
        return _model_resource(self._model_registry, config)

'''
replace_once(
    service,
    "    async def _task_ids(self) -> tuple[str, ...]:\n",
    model_methods + "    async def _task_ids(self) -> tuple[str, ...]:\n",
)
resource_helpers = '''def _model_provider_resource(
    registry: ModelRegistry,
    provider: ModelProvider,
) -> dict[str, JsonValue]:
    descriptor = provider.descriptor
    return {
        "id": descriptor.provider_id,
        "type": "model-provider",
        "provider_type": descriptor.provider_type,
        "contract_version": descriptor.contract_version,
        "supported_operations": list(descriptor.supported_operations),
        "capabilities": [json_object(item) for item in descriptor.capabilities],
        "health": registry.provider_health(descriptor.provider_id).value,
        "enabled": registry.provider_enabled(descriptor.provider_id),
        "available": descriptor.available,
        "limits": dict(descriptor.limits),
        "resources": dict(descriptor.resources),
        "adapter_metadata": [json_object(item) for item in descriptor.adapter_metadata],
    }


def _model_resource(
    registry: ModelRegistry,
    config: ModelConfiguration,
) -> dict[str, JsonValue]:
    resource = json_object(config)
    resource["id"] = config.config_id
    resource["type"] = "model"
    resource["effective_health"] = registry.effective_health(config).value
    return resource


'''
replace_once(
    service,
    "def _event_resource(event: object) -> dict[str, JsonValue]:\n",
    resource_helpers + "def _event_resource(event: object) -> dict[str, JsonValue]:\n",
)

http = "src/ai_multi_agent_platform/control_plane/http.py"
replace_once(
    http,
    '                            "timeline",\n',
    '                            "timeline",\n                            "model-providers",\n                            "models",\n',
)
replace_once(
    http,
    '            if segments[0] in {"plans", "steps", "artifacts", "results"}:\n',
    '''            if segments[0] == "model-providers":
                return await self._model_providers(
                    request, context, query, segments, request_id, correlation_id
                )
            if segments[0] == "models":
                return await self._models(
                    request, context, query, segments, request_id, correlation_id
                )
            if segments[0] in {"plans", "steps", "artifacts", "results"}:
''',
)
http_methods = '''    async def _model_providers(
        self,
        request: HTTPRequest,
        context: RequestContext,
        query: PageQuery,
        segments: list[str],
        request_id: str,
        correlation_id: str,
    ) -> HTTPResponse:
        if len(segments) == 1 and request.method == "GET":
            page = await self._control_plane.list_model_providers(context, query)
            return self._response(200, page, request_id, correlation_id)
        if len(segments) == 2 and ":" in segments[1] and request.method == "POST":
            provider_id, command = segments[1].rsplit(":", 1)
            if command == "enable":
                item = await self._control_plane.set_model_provider_enabled(
                    context, provider_id, enabled=True
                )
            elif command == "disable":
                item = await self._control_plane.set_model_provider_enabled(
                    context, provider_id, enabled=False
                )
            elif command == "refresh-health":
                item = await self._control_plane.refresh_model_provider_health(
                    context, provider_id
                )
            else:
                raise APIException(
                    status=404,
                    code="not_found",
                    message="unknown model-provider command",
                )
            return self._response(200, item, request_id, correlation_id)
        if len(segments) == 2 and request.method == "GET":
            item = await self._control_plane.get_model_provider(context, segments[1])
            return self._response(200, item, request_id, correlation_id)
        raise APIException(status=405, code="method_not_allowed", message="method not allowed")

    async def _models(
        self,
        request: HTTPRequest,
        context: RequestContext,
        query: PageQuery,
        segments: list[str],
        request_id: str,
        correlation_id: str,
    ) -> HTTPResponse:
        if len(segments) == 1 and request.method == "GET":
            page = await self._control_plane.list_models(context, query)
            return self._response(200, page, request_id, correlation_id)
        if len(segments) == 2 and ":" in segments[1] and request.method == "POST":
            model_id, command = segments[1].rsplit(":", 1)
            if command == "enable":
                item = await self._control_plane.set_model_enabled(
                    context, model_id, enabled=True
                )
            elif command == "disable":
                item = await self._control_plane.set_model_enabled(
                    context, model_id, enabled=False
                )
            else:
                raise APIException(
                    status=404,
                    code="not_found",
                    message="unknown model command",
                )
            return self._response(200, item, request_id, correlation_id)
        if len(segments) == 2 and request.method == "GET":
            item = await self._control_plane.get_model(context, segments[1])
            return self._response(200, item, request_id, correlation_id)
        raise APIException(status=405, code="method_not_allowed", message="method not allowed")

'''
replace_once(
    http,
    "    async def _references(\n",
    http_methods + "    async def _references(\n",
)

openapi = "src/ai_multi_agent_platform/control_plane/openapi.py"
inventory_paths = '''    paths.update(
        {
            f"/api/{API_VERSION}/model-providers": {
                "get": _list_operation("listModelProviders", "Model provider page")
            },
            f"/api/{API_VERSION}/model-providers/{{provider_id}}": {
                "get": _read_operation(
                    "getModelProvider", "provider_id", "Model provider"
                )
            },
            f"/api/{API_VERSION}/models": {
                "get": _list_operation("listModels", "Model configuration page")
            },
            f"/api/{API_VERSION}/models/{{model_id}}": {
                "get": _read_operation("getModel", "model_id", "Model configuration")
            },
        }
    )

    for command in ("enable", "disable", "refresh-health"):
        paths[f"/api/{API_VERSION}/model-providers/{{provider_id}}:{command}"] = {
            "post": {
                **_operation(
                    f"{command.replace('-', ' ').title().replace(' ', '')}ModelProvider",
                    "Updated model provider",
                ),
                "parameters": [
                    _path_parameter("provider_id"),
                    {"$ref": "#/components/parameters/IdempotencyKey"},
                ],
            }
        }

    for command in ("enable", "disable"):
        paths[f"/api/{API_VERSION}/models/{{model_id}}:{command}"] = {
            "post": {
                **_operation(f"{command}Model", "Updated model configuration"),
                "parameters": [
                    _path_parameter("model_id"),
                    {"$ref": "#/components/parameters/IdempotencyKey"},
                ],
            }
        }

'''
replace_once(
    openapi,
    '    for command in ("queue", "start", "cancel", "retry"):\n',
    inventory_paths + '    for command in ("queue", "start", "cancel", "retry"):\n',
)

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
from ai_multi_agent_platform.control_plane import ControlPlane, ControlPlaneHTTP, HTTPRequest, build_openapi
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.models import (
    ModelCapabilities,
    ModelConfiguration,
    ModelLocation,
    ModelRegistry,
)
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeModelProvider, FakeOrchestrator


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
''')

docs = Path("docs/MODELS.md")
text = docs.read_text()
marker = "## Remaining #10 work\n"
if marker not in text:
    raise SystemExit("MODELS.md completion marker not found")
prefix = text.split(marker, 1)[0]
docs.write_text(prefix + '''## Control Plane inventory

The versioned Control Plane exposes the canonical model inventory without turning provider-native APIs into northbound contracts:

- `GET /api/v1/model-providers`
- `GET /api/v1/model-providers/{provider_id}`
- `POST /api/v1/model-providers/{provider_id}:enable`
- `POST /api/v1/model-providers/{provider_id}:disable`
- `POST /api/v1/model-providers/{provider_id}:refresh-health`
- `GET /api/v1/models`
- `GET /api/v1/models/{model_id_or_alias}`
- `POST /api/v1/models/{model_id_or_alias}:enable`
- `POST /api/v1/models/{model_id_or_alias}:disable`

Inventory mutations require the Control Plane idempotency key. Provider construction and provider-native configuration remain adapter/bootstrap responsibilities rather than generic HTTP object creation.

## Issue #10 completion state

The #10 baseline now includes the distinct provider, registry and router contracts; stable canonical model configuration IDs; persistent reference storage; deterministic capability/location/health routing; rich canonical request/response types; local OpenAI-compatible execution; timeout/cancellation/error normalization; configuration examples; model/provider Control Plane inventory; and end-to-end/contract coverage.

The baseline remains local-first and does not require any recurring paid AI/API service. Optional gateways and additional commercial or local providers remain replaceable follow-up adapters.
''')
