from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator

import pytest

from ai_multi_agent_platform.adapters.openai_compatible import OpenAICompatibleProviderConfig
from ai_multi_agent_platform.adapters.openai_compatible_streaming import (
    OpenAICompatibleModelProvider,
)
from ai_multi_agent_platform.contracts import (
    JsonValue,
    ModelRequest,
    ModelResponse,
    ModelStreamEvent,
    ModelStreamEventKind,
    OperationContext,
)


@pytest.mark.integration
def test_live_bifrost_openai_compatible_streaming_when_configured() -> None:
    provider = _live_provider_or_skip()
    canonical_model_id = "issue-859-live-bifrost-model"

    events = asyncio.run(
        _collect(
            provider.stream(
                ModelRequest(
                    request_id="issue-859-stream",
                    messages=("Reply with the single word: ready",),
                    context=OperationContext(correlation_id="issue-859:stream"),
                    requirements={"model_config_id": canonical_model_id},
                )
            )
        )
    )

    assert events
    assert any(event.kind is ModelStreamEventKind.TEXT_DELTA for event in events)
    completed = [event for event in events if event.kind is ModelStreamEventKind.COMPLETED]
    assert len(completed) == 1
    assert all(event.model_ref == canonical_model_id for event in events)


@pytest.mark.integration
def test_live_bifrost_structured_output_when_declared_supported() -> None:
    if os.getenv("BIFROST_EVAL_STRUCTURED_OUTPUT") != "1":
        pytest.skip("set BIFROST_EVAL_STRUCTURED_OUTPUT=1 for a model with JSON-mode support")
    provider = _live_provider_or_skip()
    canonical_model_id = "issue-859-live-bifrost-model"

    response = asyncio.run(
        provider.generate(
            ModelRequest(
                request_id="issue-859-structured",
                messages=('Return exactly this JSON object: {"status":"ready"}',),
                context=OperationContext(correlation_id="issue-859:structured"),
                requirements={
                    "model_config_id": canonical_model_id,
                    "structured_output": True,
                },
            )
        )
    )

    assert response.model_ref == canonical_model_id
    protocol = _protocol_metadata(response)
    assert protocol.get("structured_output") == {"status": "ready"}


@pytest.mark.integration
def test_live_bifrost_tool_calling_when_declared_supported() -> None:
    if os.getenv("BIFROST_EVAL_TOOL_CALLING") != "1":
        pytest.skip("set BIFROST_EVAL_TOOL_CALLING=1 for a model with tool-calling support")
    provider = _live_provider_or_skip()
    canonical_model_id = "issue-859-live-bifrost-model"

    response = asyncio.run(
        provider.generate(
            ModelRequest(
                request_id="issue-859-tool",
                messages=(
                    "Call the report_status tool with status ready. Do not answer directly.",
                ),
                context=OperationContext(correlation_id="issue-859:tool"),
                requirements={
                    "model_config_id": canonical_model_id,
                    "canonical_tools": [
                        {
                            "name": "report_status",
                            "description": "Report the requested readiness status.",
                            "input_schema": {
                                "type": "object",
                                "properties": {"status": {"type": "string"}},
                                "required": ["status"],
                                "additionalProperties": False,
                            },
                        }
                    ],
                },
            )
        )
    )

    assert response.model_ref == canonical_model_id
    protocol = _protocol_metadata(response)
    tool_calls = protocol.get("tool_calls")
    assert isinstance(tool_calls, list)
    assert tool_calls
    first = tool_calls[0]
    assert isinstance(first, dict)
    assert first.get("tool_name") == "report_status"


async def _collect(stream: AsyncIterator[ModelStreamEvent]) -> list[ModelStreamEvent]:
    events: list[ModelStreamEvent] = []
    async for event in stream:
        events.append(event)
    return events


def _live_provider_or_skip() -> OpenAICompatibleModelProvider:
    base_url = os.getenv("BIFROST_EVAL_BIFROST_BASE_URL")
    native_model = os.getenv("BIFROST_EVAL_BIFROST_MODEL")
    if not base_url or not native_model:
        pytest.skip("live #859 Bifrost endpoint is not configured")
    return OpenAICompatibleModelProvider(
        OpenAICompatibleProviderConfig(
            provider_id="issue-859-live-bifrost",
            base_url=base_url,
            models={"issue-859-live-bifrost-model": native_model},
            api_key_env=os.getenv("BIFROST_EVAL_BIFROST_API_KEY_ENV"),
        )
    )


def _protocol_metadata(response: ModelResponse) -> dict[str, JsonValue]:
    for metadata in response.adapter_metadata:
        if metadata.namespace == "model-protocol":
            return dict(metadata.values)
    raise AssertionError("model-protocol metadata missing")
