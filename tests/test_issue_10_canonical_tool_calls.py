from __future__ import annotations

import asyncio
from collections.abc import Mapping

from ai_multi_agent_platform.adapters import (
    HttpJsonResponse,
    OpenAICompatibleModelProvider,
    OpenAICompatibleProviderConfig,
)
from ai_multi_agent_platform.contracts import HealthStatus, JsonValue, OperationContext
from ai_multi_agent_platform.models import (
    CanonicalModelRequest,
    ModelCapabilities,
    ModelConfiguration,
    ModelLocation,
    ModelMessage,
    ModelRegistry,
    ModelRole,
    ModelRuntime,
)


class ToolCallTransport:
    async def request_json(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, JsonValue] | None,
        timeout_seconds: float,
    ) -> HttpJsonResponse:
        del method, headers, payload, timeout_seconds
        if url.endswith("/models"):
            return HttpJsonResponse(200, {"data": [{"id": "native-tool-model"}]})
        return HttpJsonResponse(
            200,
            {
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {
                                        "name": "lookup",
                                        "arguments": "{\"key\":\"value\"}",
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            },
        )


def test_openai_tool_call_is_normalized_to_canonical_response() -> None:
    provider = OpenAICompatibleModelProvider(
        OpenAICompatibleProviderConfig(
            provider_id="tool-provider",
            base_url="http://127.0.0.1:8000/v1",
            models={"model-tool": "native-tool-model"},
        ),
        transport=ToolCallTransport(),
    )
    registry = ModelRegistry()
    registry.register_provider(provider)
    registry.register_model(
        ModelConfiguration(
            config_id="model-tool",
            display_name="Tool model",
            provider_id="tool-provider",
            location=ModelLocation.LOCAL,
            health=HealthStatus.HEALTHY,
            capabilities=ModelCapabilities(tool_calling=True, modalities=("text",)),
        )
    )
    asyncio.run(registry.refresh_health())

    response = asyncio.run(
        ModelRuntime(registry).generate_canonical(
            CanonicalModelRequest(
                request_id="request-tool-call",
                context=OperationContext(correlation_id="corr-tool-call"),
                messages=(ModelMessage.text(ModelRole.USER, "Use the tool."),),
                routing_requirements={"local_only": True, "tool_calling": True},
            )
        )
    )

    assert response.finish_reason.value == "tool_call"
    assert response.content == ()
    assert len(response.tool_calls) == 1
    tool_call = response.tool_calls[0]
    assert tool_call.call_id == "call-1"
    assert tool_call.tool_name == "lookup"
    assert tool_call.arguments == {"key": "value"}
