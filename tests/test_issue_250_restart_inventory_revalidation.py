from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path

from ai_multi_agent_platform.adapters import HttpJsonResponse
from ai_multi_agent_platform.adapters.onboarding_openai_compatible import (
    OpenAICompatibleOnboardingAdapter,
)
from ai_multi_agent_platform.agents import AgentRuntime, AgentService, InMemoryAgentRepository
from ai_multi_agent_platform.contracts import HealthStatus, JsonValue
from ai_multi_agent_platform.control_plane import ActorContext, RequestContext, ScopeStore
from ai_multi_agent_platform.models import JsonModelRegistryStore, ModelRegistry
from ai_multi_agent_platform.onboarding import (
    FIRST_RUN_RESOURCE_ID,
    JsonModelProviderSetupStore,
    OnboardingService,
)


class MutableInventoryTransport:
    def __init__(self, model_ids: tuple[str, ...]) -> None:
        self.model_ids = model_ids
        self.calls: list[tuple[str, str]] = []

    async def request_json(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, JsonValue] | None,
        timeout_seconds: float,
    ) -> HttpJsonResponse:
        del headers, payload, timeout_seconds
        self.calls.append((method, url))
        return HttpJsonResponse(
            status_code=200,
            payload={"data": [{"id": model_id} for model_id in self.model_ids]},
        )


def _context() -> RequestContext:
    return RequestContext(
        request_id="issue-250-restart-inventory-request",
        correlation_id="issue-250-restart-inventory-correlation",
        idempotency_key="issue-250-restart-inventory-command",
        actor=ActorContext(
            principal_ref="user-alice",
            owner_type="user",
            owner_id="user-alice",
            actor_type="human",
        ),
    )


def _service(tmp_path: Path, transport: MutableInventoryTransport) -> OnboardingService:
    models = ModelRegistry()
    agents = AgentService(InMemoryAgentRepository())
    service = OnboardingService(
        models=models,
        model_store=JsonModelRegistryStore(tmp_path / "models.json"),
        provider_store=JsonModelProviderSetupStore(tmp_path / "model-providers.json"),
        scopes=ScopeStore(),
        agents=agents,
        agent_runtime=AgentRuntime(agents, model_registry=models),
        model_adapters=(OpenAICompatibleOnboardingAdapter(transport=transport),),
    )
    service.restore()
    return service


def _payload() -> dict[str, JsonValue]:
    return {
        "adapter_id": "openai-compatible",
        "provider_id": "local-openai",
        "model_config_id": "model-qwen-local",
        "provider_model": "qwen-local",
        "display_name": "Qwen Local",
        "base_url": "http://127.0.0.1:8001/v1",
        "location": "local",
        "capabilities": {
            "context_window": 32768,
            "tool_calling": True,
            "structured_output": True,
            "streaming": False,
            "modalities": ["text"],
        },
    }


def test_restart_health_revalidation_requires_configured_native_model_to_still_exist(
    tmp_path: Path,
) -> None:
    first_transport = MutableInventoryTransport(("qwen-local",))
    first = _service(tmp_path, first_transport)
    asyncio.run(first.configure_model(_context(), FIRST_RUN_RESOURCE_ID, _payload()))
    assert first.status(_context())["state"] == "needs_project"

    restarted_transport = MutableInventoryTransport(("different-model",))
    restarted = _service(tmp_path, restarted_transport)
    assert restarted.status(_context())["state"] == "needs_model"

    missing_model_health = asyncio.run(restarted.models.refresh_health("local-openai"))

    assert missing_model_health["local-openai"] is HealthStatus.UNAVAILABLE
    assert restarted.models.provider_health("local-openai") is HealthStatus.UNAVAILABLE
    assert restarted.status(_context())["state"] == "needs_model"

    restarted_transport.model_ids = ("qwen-local",)
    restored_model_health = asyncio.run(restarted.models.refresh_health("local-openai"))

    assert restored_model_health["local-openai"] is HealthStatus.HEALTHY
    assert restarted.status(_context())["state"] == "needs_project"
