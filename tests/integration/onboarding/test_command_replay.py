from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from ai_multi_agent_platform.adapters import HttpJsonResponse
from ai_multi_agent_platform.adapters.onboarding_openai_compatible import (
    OpenAICompatibleOnboardingAdapter,
)
from ai_multi_agent_platform.agents import AgentRuntime, AgentService, InMemoryAgentRepository
from ai_multi_agent_platform.contracts import ContractError, ErrorCode, JsonValue
from ai_multi_agent_platform.control_plane import ActorContext, RequestContext, ScopeStore
from ai_multi_agent_platform.models import JsonModelRegistryStore, ModelRegistry
from ai_multi_agent_platform.onboarding import (
    FIRST_RUN_RESOURCE_ID,
    JsonModelProviderSetupStore,
    JsonOnboardingCommandStore,
    OnboardingService,
)


class ReplayTransport:
    def __init__(self) -> None:
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
            payload={"data": [{"id": "qwen-local"}]},
        )


def _context(idempotency_key: str = "issue-250-replay") -> RequestContext:
    return RequestContext(
        request_id=f"request:{idempotency_key}",
        correlation_id=f"correlation:{idempotency_key}",
        idempotency_key=idempotency_key,
        actor=ActorContext(
            principal_ref="user-alice",
            owner_type="user",
            owner_id="user-alice",
            actor_type="human",
        ),
    )


def _payload(**overrides: JsonValue) -> dict[str, JsonValue]:
    payload: dict[str, JsonValue] = {
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
    payload.update(overrides)
    return payload


def _service(tmp_path: Path, transport: ReplayTransport) -> OnboardingService:
    models = ModelRegistry()
    agents = AgentService(InMemoryAgentRepository())
    service = OnboardingService(
        models=models,
        model_store=JsonModelRegistryStore(tmp_path / "models.json"),
        provider_store=JsonModelProviderSetupStore(tmp_path / "model-providers.json"),
        command_store=JsonOnboardingCommandStore(tmp_path / "onboarding-commands.json"),
        scopes=ScopeStore(),
        agents=agents,
        agent_runtime=AgentRuntime(agents, model_registry=models),
        model_adapters=(OpenAICompatibleOnboardingAdapter(transport=transport),),
    )
    service.restore()
    return service


def test_configure_model_replay_survives_restart_without_second_provider_call(
    tmp_path: Path,
) -> None:
    first_transport = ReplayTransport()
    first = _service(tmp_path, first_transport)
    payload = _payload()

    first_result = asyncio.run(first.configure_model(_context(), FIRST_RUN_RESOURCE_ID, payload))
    first_revision = first.models.get_model("model-qwen-local").revision
    assert first_transport.calls

    restarted_transport = ReplayTransport()
    restarted = _service(tmp_path, restarted_transport)
    replayed_result = asyncio.run(
        restarted.configure_model(_context(), FIRST_RUN_RESOURCE_ID, payload)
    )

    assert replayed_result == first_result
    assert restarted_transport.calls == []
    assert restarted.models.get_model("model-qwen-local").revision == first_revision
    command_document = json.loads(
        (tmp_path / "onboarding-commands.json").read_text(encoding="utf-8")
    )
    serialized = json.dumps(command_document, sort_keys=True)
    assert "qwen-local" in serialized
    assert "api_key" not in serialized.casefold()
    assert "bearer_token" not in serialized.casefold()
    assert "password" not in serialized.casefold()


def test_reusing_idempotency_key_with_different_payload_conflicts_before_provider_call(
    tmp_path: Path,
) -> None:
    first = _service(tmp_path, ReplayTransport())
    asyncio.run(first.configure_model(_context(), FIRST_RUN_RESOURCE_ID, _payload()))

    restarted_transport = ReplayTransport()
    restarted = _service(tmp_path, restarted_transport)

    with pytest.raises(ContractError) as caught:
        asyncio.run(
            restarted.configure_model(
                _context(),
                FIRST_RUN_RESOURCE_ID,
                _payload(display_name="Different configuration"),
            )
        )

    assert caught.value.code is ErrorCode.CONFLICT
    assert restarted_transport.calls == []
    assert restarted.models.get_model("model-qwen-local").revision == 1


def test_nested_plaintext_credentials_in_lists_are_rejected_before_provider_call(
    tmp_path: Path,
) -> None:
    transport = ReplayTransport()
    service = _service(tmp_path, transport)

    with pytest.raises(ContractError) as caught:
        asyncio.run(
            service.configure_model(
                _context(),
                FIRST_RUN_RESOURCE_ID,
                _payload(
                    adapter_options=[
                        {"headers": [{"name": "Authorization", "token": "do-not-send"}]}
                    ]
                ),
            )
        )

    assert caught.value.code is ErrorCode.INVALID_REQUEST
    assert caught.value.details["field"] == "payload.adapter_options[0].headers[0].token"
    assert transport.calls == []
    assert not (tmp_path / "onboarding-commands.json").exists()
    assert not (tmp_path / "model-providers.json").exists()
