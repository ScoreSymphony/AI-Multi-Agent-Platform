from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

import ai_multi_agent_platform.adapters.litellm as litellm_adapter
from ai_multi_agent_platform.adapters.litellm import (
    LiteLLMMode,
    LiteLLMModelProvider,
    LiteLLMProviderConfig,
    LiteLLMTelemetryMode,
)
from ai_multi_agent_platform.configuration import (
    ConfigLayer,
    ConfigScope,
    ConfigSource,
    ConfigurationResolver,
    ConfigurationSchema,
)
from ai_multi_agent_platform.contracts import (
    ContractError,
    ErrorCode,
    HealthStatus,
    ModelRequest,
    OperationContext,
)
from ai_multi_agent_platform.models import (
    DeterministicModelRouter,
    ModelConfiguration,
    ModelLocation,
    ModelRegistry,
)

CTX = OperationContext(correlation_id="corr-litellm-hardening")


async def successful_completion(**kwargs: object) -> object:
    return {
        "choices": [
            {
                "message": {"role": "assistant", "content": "ok"},
                "finish_reason": "stop",
            }
        ]
    }


def test_committed_examples_flow_through_platform_configuration() -> None:
    config_file = Path(__file__).parents[1] / "config" / "litellm.example.json"
    examples = json.loads(config_file.read_text())
    raw_library = examples["library_local_example"]

    schema = ConfigurationSchema(
        version="issue-11-v1",
        json_schema={
            "type": "object",
            "properties": {"litellm": {"type": "object"}},
            "required": ["litellm"],
            "additionalProperties": False,
        },
    )
    effective = ConfigurationResolver(schema).resolve(
        (
            ConfigLayer(
                ConfigScope.PROVIDER,
                {"litellm": raw_library},
                ConfigSource("issue-11-example", "json"),
            ),
        )
    )
    raw_effective = effective.values["litellm"]
    assert isinstance(raw_effective, dict)
    parsed = LiteLLMProviderConfig.from_mapping(raw_effective)

    assert parsed.enabled is True
    assert parsed.mode is LiteLLMMode.LIBRARY
    assert parsed.max_retries == 0
    assert parsed.telemetry_mode is LiteLLMTelemetryMode.PLATFORM_ONLY


def test_from_mapping_rejects_unknown_configuration() -> None:
    with pytest.raises(ValueError, match="unknown LiteLLM configuration fields"):
        LiteLLMProviderConfig.from_mapping(
            {
                "provider_id": "litellm-test",
                "mode": "library",
                "models": {"model-local": "ollama/model-local"},
                "silent_policy_bypass": True,
            }
        )


def test_library_retry_configuration_is_forwarded_explicitly() -> None:
    captured: dict[str, object] = {}

    async def completion(**kwargs: object) -> object:
        captured.update(kwargs)
        return await successful_completion(**kwargs)

    provider = LiteLLMModelProvider(
        LiteLLMProviderConfig(
            provider_id="litellm-retries",
            mode=LiteLLMMode.LIBRARY,
            models={"model-local": "ollama/model-local"},
            max_retries=3,
        ),
        completion=completion,
    )
    asyncio.run(
        provider.generate(
            ModelRequest(
                request_id="req-retries",
                messages=("hello",),
                context=CTX,
                requirements={"model_config_id": "model-local"},
            )
        )
    )

    assert captured["num_retries"] == 3


def test_telemetry_disabled_suppresses_litellm_request_metadata() -> None:
    provider = LiteLLMModelProvider(
        LiteLLMProviderConfig(
            provider_id="litellm-no-telemetry",
            mode=LiteLLMMode.LIBRARY,
            models={"model-local": "ollama/model-local"},
            telemetry_mode=LiteLLMTelemetryMode.DISABLED,
        ),
        completion=successful_completion,
    )
    response = asyncio.run(
        provider.generate(
            ModelRequest(
                request_id="req-no-telemetry",
                messages=("hello",),
                context=CTX,
                requirements={"model_config_id": "model-local"},
            )
        )
    )

    assert {item.namespace for item in response.adapter_metadata} == {"model-protocol"}
    assert provider.descriptor.adapter_metadata[0].values["telemetry_mode"] == "disabled"


def test_enabled_library_provider_fails_before_registry_attachment_without_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing_dependency(name: str) -> object:
        assert name == "litellm"
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(litellm_adapter.importlib, "import_module", missing_dependency)
    registry = ModelRegistry()

    with pytest.raises(ContractError) as captured:
        provider = LiteLLMModelProvider(
            LiteLLMProviderConfig(
                provider_id="litellm-missing",
                mode=LiteLLMMode.LIBRARY,
                models={"model-local": "ollama/model-local"},
            )
        )
        registry.register_provider(provider)

    assert captured.value.code is ErrorCode.INVALID_CONFIGURATION
    assert registry.list_providers() == ()


def test_disabled_library_provider_does_not_load_optional_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def missing_dependency(name: str) -> object:
        calls.append(name)
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(litellm_adapter.importlib, "import_module", missing_dependency)
    provider = LiteLLMModelProvider(
        LiteLLMProviderConfig(
            provider_id="litellm-disabled",
            mode=LiteLLMMode.LIBRARY,
            models={"model-local": "ollama/model-local"},
            enabled=False,
        )
    )

    assert asyncio.run(provider.health()) is HealthStatus.UNAVAILABLE
    assert calls == []


def test_platform_router_preserves_self_hosted_only_policy() -> None:
    registry = ModelRegistry()
    self_hosted = LiteLLMModelProvider(
        LiteLLMProviderConfig(
            provider_id="litellm-self-hosted",
            mode=LiteLLMMode.LIBRARY,
            models={"model-self-hosted": "ollama/model-self-hosted"},
        ),
        completion=successful_completion,
    )
    remote = LiteLLMModelProvider(
        LiteLLMProviderConfig(
            provider_id="litellm-remote",
            mode=LiteLLMMode.LIBRARY,
            models={"model-remote": "openai/model-remote"},
        ),
        completion=successful_completion,
    )
    registry.register_provider(self_hosted)
    registry.register_provider(remote)
    asyncio.run(registry.refresh_health())
    registry.register_model(
        ModelConfiguration(
            config_id="model-self-hosted",
            display_name="Self hosted",
            provider_id="litellm-self-hosted",
            location=ModelLocation.SELF_HOSTED,
            health=HealthStatus.HEALTHY,
            priority=10,
        )
    )
    registry.register_model(
        ModelConfiguration(
            config_id="model-remote",
            display_name="Remote",
            provider_id="litellm-remote",
            location=ModelLocation.REMOTE,
            health=HealthStatus.HEALTHY,
            priority=100,
        )
    )

    selection = asyncio.run(
        DeterministicModelRouter(registry).select_provider(
            ModelRequest(
                request_id="req-self-hosted",
                messages=("hello",),
                context=CTX,
                requirements={"self_hosted_only": True},
            )
        )
    )

    assert selection.model_ref == "model-self-hosted"
    assert selection.provider_id == "litellm-self-hosted"
