from __future__ import annotations

import asyncio
from collections.abc import Mapping

from ai_multi_agent_platform.adapters import (
    HttpJsonResponse,
    OpenAICompatibleModelProvider,
    OpenAICompatibleProviderConfig,
)
from ai_multi_agent_platform.contracts import (
    AdapterMetadata,
    HealthStatus,
    JsonValue,
    OperationContext,
    OperationControl,
)
from ai_multi_agent_platform.models import (
    CanonicalModelRequest,
    ModelCapabilities,
    ModelConfiguration,
    ModelGenerationParameters,
    ModelLocation,
    ModelMessage,
    ModelRegistry,
    ModelRole,
    ModelRuntime,
    ModelToolDefinition,
    StructuredResponseExpectation,
    StructuredResponseKind,
)


class ProtocolTransport:
    def __init__(self) -> None:
        self.calls: list[
            tuple[
                str,
                str,
                Mapping[str, str],
                Mapping[str, JsonValue] | None,
                float,
            ]
        ] = []

    async def request_json(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, JsonValue] | None,
        timeout_seconds: float,
    ) -> HttpJsonResponse:
        self.calls.append((method, url, headers, payload, timeout_seconds))
        if url.endswith("/models"):
            return HttpJsonResponse(200, {"data": [{"id": "native-model"}]})
        return HttpJsonResponse(
            200,
            {
                "choices": [
                    {
                        "message": {"content": "{\"answer\":42}"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"total_tokens": 9},
            },
        )


def test_rich_request_encodes_provider_neutral_messages_tools_and_context() -> None:
    request = CanonicalModelRequest(
        request_id="request-rich",
        context=OperationContext(
            correlation_id="corr-rich",
            control=OperationControl(timeout_seconds=3.5),
        ),
        system_instruction="Follow the system instruction.",
        messages=(ModelMessage.text(ModelRole.USER, "Return the answer."),),
        tools=(
            ModelToolDefinition(
                tool_ref="tool:lookup",
                name="lookup",
                description="Lookup one value",
                input_schema={
                    "type": "object",
                    "properties": {"key": {"type": "string"}},
                },
            ),
        ),
        response=StructuredResponseExpectation(
            kind=StructuredResponseKind.JSON_SCHEMA,
            schema_name="answer",
            json_schema={
                "type": "object",
                "properties": {"answer": {"type": "integer"}},
                "required": ["answer"],
            },
            strict=True,
        ),
        generation=ModelGenerationParameters(
            temperature=0.1,
            max_tokens=200,
            stop=("END",),
        ),
        task_id="task:rich",
        run_id="run:rich",
        agent_id="agent:rich",
        routing_requirements={"local_only": True, "tool_calling": True},
    )

    contract = request.to_contract_request()

    assert contract.requirements["task_id"] == "task:rich"
    assert contract.requirements["run_id"] == "run:rich"
    assert contract.requirements["agent_id"] == "agent:rich"
    assert contract.requirements["temperature"] == 0.1
    assert contract.requirements["stop"] == ["END"]
    messages = contract.requirements["canonical_messages"]
    assert isinstance(messages, list)
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"


def test_rich_request_runs_end_to_end_through_router_registry_and_local_provider() -> None:
    transport = ProtocolTransport()
    provider = OpenAICompatibleModelProvider(
        OpenAICompatibleProviderConfig(
            provider_id="local-provider",
            base_url="http://127.0.0.1:8000/v1",
            models={"model-local": "native-model"},
        ),
        transport=transport,
    )
    registry = ModelRegistry()
    registry.register_provider(provider)
    registry.register_model(
        ModelConfiguration(
            config_id="model-local",
            display_name="Local model",
            provider_id="local-provider",
            location=ModelLocation.LOCAL,
            health=HealthStatus.HEALTHY,
            priority=10,
            capabilities=ModelCapabilities(
                context_window=32_768,
                tool_calling=True,
                structured_output=True,
                streaming=False,
                modalities=("text",),
            ),
            adapter_metadata=(
                AdapterMetadata(
                    namespace="openai-compatible",
                    values={"model": "native-model"},
                ),
            ),
        )
    )
    asyncio.run(registry.refresh_health())
    runtime = ModelRuntime(registry)

    response = asyncio.run(
        runtime.generate_canonical(
            CanonicalModelRequest(
                request_id="request-e2e",
                context=OperationContext(
                    correlation_id="corr-e2e",
                    control=OperationControl(timeout_seconds=4.0),
                ),
                messages=(ModelMessage.text(ModelRole.USER, "Return JSON."),),
                tools=(
                    ModelToolDefinition(
                        tool_ref="tool:lookup",
                        name="lookup",
                        input_schema={"type": "object"},
                    ),
                ),
                response=StructuredResponseExpectation(
                    kind=StructuredResponseKind.JSON_OBJECT
                ),
                routing_requirements={
                    "local_only": True,
                    "tool_calling": True,
                    "structured_output": True,
                },
                task_id="task:e2e",
                run_id="run:e2e",
                agent_id="agent:e2e",
            )
        )
    )

    assert response.model_config_id == "model-local"
    assert response.finish_reason.value == "stop"
    assert response.usage["total_tokens"] == 9
    generation_call = transport.calls[-1]
    assert generation_call[4] == 4.0
    payload = generation_call[3]
    assert payload is not None
    assert payload["model"] == "native-model"
    assert payload["response_format"] == {"type": "json_object"}
    assert "tools" in payload
    messages = payload["messages"]
    assert isinstance(messages, list)
    assert messages[0]["role"] == "user"
