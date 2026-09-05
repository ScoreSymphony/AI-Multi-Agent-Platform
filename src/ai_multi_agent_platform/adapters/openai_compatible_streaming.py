"""Native streaming extension for the local OpenAI-compatible model provider."""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import replace
from time import perf_counter, time
from typing import Protocol, cast, runtime_checkable
from urllib import error, request

from ai_multi_agent_platform.contracts import (
    AdapterMetadata,
    ContractError,
    ErrorCode,
    JsonValue,
    ModelRequest,
    ModelResponse,
    ModelStreamEvent,
    ModelStreamEventKind,
    ProviderDescriptor,
)

from .openai_compatible import (
    HttpJsonResponse,
    OpenAICompatibleModelProvider as _BaseOpenAICompatibleModelProvider,
    OpenAICompatibleProviderConfig,
    OpenAICompatibleTransport,
    UrllibOpenAICompatibleTransport,
)


@runtime_checkable
class OpenAICompatibleStreamingTransport(Protocol):
    """Optional transport seam for OpenAI-compatible SSE responses."""

    def stream_json(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, JsonValue] | None,
        timeout_seconds: float,
    ) -> AsyncIterator[HttpJsonResponse]: ...


class UrllibOpenAICompatibleStreamingTransport(UrllibOpenAICompatibleTransport):
    """Standard-library JSON transport with an SSE streaming extension."""

    def stream_json(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, JsonValue] | None,
        timeout_seconds: float,
    ) -> AsyncIterator[HttpJsonResponse]:
        async def iterate() -> AsyncIterator[HttpJsonResponse]:
            loop = asyncio.get_running_loop()
            queue: asyncio.Queue[tuple[str, object]] = asyncio.Queue()
            stop = threading.Event()

            def emit(tag: str, value: object) -> None:
                loop.call_soon_threadsafe(queue.put_nowait, (tag, value))

            def producer() -> None:
                body = None
                if payload is not None:
                    body = json.dumps(dict(payload), separators=(",", ":")).encode("utf-8")
                http_request = request.Request(
                    url,
                    data=body,
                    headers=dict(headers),
                    method=method,
                )
                try:
                    with request.urlopen(http_request, timeout=timeout_seconds) as response:
                        response_headers = dict(response.headers.items())
                        event_lines: list[str] = []
                        for raw_line in response:
                            if stop.is_set():
                                break
                            line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
                            if not line:
                                if self._emit_sse_event(
                                    event_lines,
                                    response.status,
                                    response_headers,
                                    emit,
                                ):
                                    break
                                event_lines.clear()
                                continue
                            if line.startswith(":"):
                                continue
                            if line.startswith("data:"):
                                event_lines.append(line[5:].lstrip())
                        if event_lines and not stop.is_set():
                            self._emit_sse_event(
                                event_lines,
                                response.status,
                                response_headers,
                                emit,
                            )
                except error.HTTPError as exc:
                    raw = exc.read().decode("utf-8", errors="replace")
                    try:
                        parsed: object = json.loads(raw) if raw else None
                    except json.JSONDecodeError:
                        parsed = raw
                    emit(
                        "response",
                        HttpJsonResponse(
                            status_code=exc.code,
                            payload=(
                                cast(JsonValue, parsed) if _is_json_value(parsed) else str(parsed)
                            ),
                            headers=dict(exc.headers.items()) if exc.headers is not None else {},
                        ),
                    )
                except Exception as exc:
                    emit("error", exc)
                finally:
                    emit("done", None)

            producer_task = asyncio.create_task(asyncio.to_thread(producer))
            try:
                while True:
                    tag, value = await queue.get()
                    if tag == "done":
                        break
                    if tag == "error":
                        assert isinstance(value, Exception)
                        raise value
                    assert tag == "response"
                    assert isinstance(value, HttpJsonResponse)
                    yield value
            finally:
                stop.set()
                if not producer_task.done():
                    producer_task.cancel()
                await asyncio.gather(producer_task, return_exceptions=True)

        return iterate()

    @staticmethod
    def _emit_sse_event(
        lines: list[str],
        status_code: int,
        headers: Mapping[str, str],
        emit: Callable[[str, object], None],
    ) -> bool:
        if not lines:
            return False
        data = "\n".join(lines)
        if data == "[DONE]":
            return True
        try:
            parsed: object = json.loads(data)
        except json.JSONDecodeError as exc:
            emit("error", exc)
            return True
        if not _is_json_value(parsed):
            emit("error", ValueError("SSE payload is not JSON-compatible"))
            return True
        emit(
            "response",
            HttpJsonResponse(
                status_code=status_code,
                payload=cast(JsonValue, parsed),
                headers=headers,
            ),
        )
        return False


class OpenAICompatibleModelProvider(_BaseOpenAICompatibleModelProvider):
    """OpenAI-compatible provider with native SSE streaming when transport supports it."""

    def __init__(
        self,
        config: OpenAICompatibleProviderConfig,
        *,
        transport: OpenAICompatibleTransport | None = None,
    ) -> None:
        super().__init__(
            config,
            transport=transport or UrllibOpenAICompatibleStreamingTransport(),
        )

    @property
    def descriptor(self) -> ProviderDescriptor:
        descriptor = super().descriptor
        capabilities = tuple(
            replace(
                capability,
                supported_operations=tuple(
                    dict.fromkeys((*capability.supported_operations, "stream"))
                ),
                features=tuple(dict.fromkeys((*capability.features, "streaming"))),
            )
            for capability in descriptor.capabilities
        )
        return replace(
            descriptor,
            supported_operations=tuple(dict.fromkeys((*descriptor.supported_operations, "stream"))),
            capabilities=capabilities,
        )

    def stream(self, request_data: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        if not isinstance(self.transport, OpenAICompatibleStreamingTransport):
            return self._fallback_stream(request_data)
        return self._native_stream(request_data, self.transport)

    async def _fallback_stream(
        self,
        request_data: ModelRequest,
    ) -> AsyncIterator[ModelStreamEvent]:
        response = await self.generate(request_data)
        if response.text:
            yield ModelStreamEvent(
                kind=ModelStreamEventKind.TEXT_DELTA,
                request_id=response.request_id,
                model_ref=response.model_ref,
                text_delta=response.text,
            )
        yield ModelStreamEvent(
            kind=ModelStreamEventKind.COMPLETED,
            request_id=response.request_id,
            model_ref=response.model_ref,
            usage=dict(response.usage),
            response=response,
        )

    async def _native_stream(
        self,
        request_data: ModelRequest,
        transport: OpenAICompatibleStreamingTransport,
    ) -> AsyncIterator[ModelStreamEvent]:
        canonical_model_id = self._canonical_model_id(request_data)
        try:
            native_model = self.config.models[canonical_model_id]
        except KeyError as exc:
            raise ContractError(
                ErrorCode.MODEL_UNAVAILABLE,
                f"model is not configured for provider: {canonical_model_id}",
                provider_id=self.config.provider_id,
                details={"model_config_id": canonical_model_id},
            ) from exc

        payload: dict[str, JsonValue] = {
            "model": native_model,
            "messages": self._messages(request_data),
            "stream": True,
        }
        self._copy_generation_parameters(request_data, payload)
        self._copy_tools(request_data, payload)
        self._copy_response_expectation(request_data, payload)

        effective_timeout = (
            request_data.context.control.timeout_seconds or self.config.timeout_seconds
        )
        headers = self._headers()
        headers["Accept"] = "text/event-stream"
        url = f"{self.config.base_url.rstrip('/')}/chat/completions"
        started_wall_ms = time() * 1000.0
        started = perf_counter()
        content_parts: list[str] = []
        usage: dict[str, JsonValue] = {}
        finish_reason = "unknown"
        raw_tool_calls: dict[int, dict[str, JsonValue]] = {}
        saw_payload = False

        try:
            async with asyncio.timeout(effective_timeout):
                async for response in transport.stream_json(
                    "POST",
                    url,
                    headers=headers,
                    payload=payload,
                    timeout_seconds=effective_timeout,
                ):
                    self._raise_for_status(response)
                    body = response.payload
                    if not isinstance(body, dict):
                        raise self._invalid_response("streaming chat chunk must be a JSON object")
                    saw_payload = True
                    self._capture_usage(body.get("usage"), usage)

                    choices = body.get("choices")
                    if choices in (None, []):
                        continue
                    if not isinstance(choices, list):
                        raise self._invalid_response("streaming chat choices must be a list")
                    first = choices[0]
                    if not isinstance(first, dict):
                        raise self._invalid_response("streaming chat choice must be an object")
                    delta = first.get("delta")
                    if not isinstance(delta, dict):
                        raise self._invalid_response("streaming chat choice requires delta")

                    content = delta.get("content")
                    if content is not None:
                        if not isinstance(content, str):
                            raise self._invalid_response("streaming content delta must be text")
                        if content:
                            content_parts.append(content)
                            yield ModelStreamEvent(
                                kind=ModelStreamEventKind.TEXT_DELTA,
                                request_id=request_data.request_id,
                                model_ref=canonical_model_id,
                                text_delta=content,
                                adapter_metadata=(
                                    AdapterMetadata(
                                        namespace="openai-compatible",
                                        values={
                                            "provider_native_model": native_model,
                                            "correlation_id": request_data.context.correlation_id,
                                        },
                                    ),
                                ),
                            )

                    self._merge_tool_call_deltas(delta.get("tool_calls"), raw_tool_calls)
                    raw_finish = first.get("finish_reason")
                    if isinstance(raw_finish, str):
                        finish_reason = self._normalize_finish_reason(raw_finish)
        except asyncio.CancelledError as exc:
            raise ContractError(
                ErrorCode.CANCELLED,
                "model request was cancelled",
                provider_id=self.config.provider_id,
            ) from exc
        except TimeoutError as exc:
            raise ContractError(
                ErrorCode.TIMEOUT,
                "model provider request timed out",
                retryable=True,
                provider_id=self.config.provider_id,
            ) from exc
        except OSError as exc:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                "model provider endpoint is unavailable",
                retryable=True,
                provider_id=self.config.provider_id,
                adapter_metadata=(
                    AdapterMetadata(
                        namespace="openai-compatible",
                        values={"exception_type": type(exc).__name__},
                    ),
                ),
            ) from exc

        if not saw_payload:
            raise self._invalid_response("streaming chat completion returned no data")

        elapsed_ms = (perf_counter() - started) * 1000.0
        ended_wall_ms = time() * 1000.0
        content = "".join(content_parts)
        assembled_tool_calls = [raw_tool_calls[index] for index in sorted(raw_tool_calls)]
        canonical_tool_calls = self._canonical_tool_calls(assembled_tool_calls)
        structured_output = self._structured_output(request_data, content)

        adapter_values: dict[str, JsonValue] = {
            "provider_native_model": native_model,
            "correlation_id": request_data.context.correlation_id,
            "latency_ms": elapsed_ms,
            "started_unix_ms": started_wall_ms,
            "ended_unix_ms": ended_wall_ms,
            "status": "success",
            "finish_reason": finish_reason,
            "streaming": True,
        }
        self._copy_context_metadata(request_data, adapter_values)

        protocol_values: dict[str, JsonValue] = {
            "correlation_id": request_data.context.correlation_id,
            "latency_ms": elapsed_ms,
            "started_unix_ms": started_wall_ms,
            "ended_unix_ms": ended_wall_ms,
            "finish_reason": finish_reason,
            "streaming": True,
        }
        self._copy_context_metadata(request_data, protocol_values)
        if canonical_tool_calls:
            protocol_values["tool_calls"] = canonical_tool_calls
        if structured_output is not None:
            protocol_values["structured_output"] = structured_output

        final_response = ModelResponse(
            request_id=request_data.request_id,
            text=content,
            model_ref=canonical_model_id,
            usage=usage,
            adapter_metadata=(
                AdapterMetadata(namespace="openai-compatible", values=adapter_values),
                AdapterMetadata(namespace="model-protocol", values=protocol_values),
            ),
        )
        yield ModelStreamEvent(
            kind=ModelStreamEventKind.COMPLETED,
            request_id=request_data.request_id,
            model_ref=canonical_model_id,
            finish_reason=finish_reason,
            usage=dict(usage),
            response=final_response,
        )

    def _capture_usage(self, raw_usage: object, target: dict[str, JsonValue]) -> None:
        if not isinstance(raw_usage, dict):
            return
        for key, value in raw_usage.items():
            if isinstance(key, str) and self._is_json_value(value):
                target[key] = cast(JsonValue, value)

    def _merge_tool_call_deltas(
        self,
        value: object,
        target: dict[int, dict[str, JsonValue]],
    ) -> None:
        if value is None:
            return
        if not isinstance(value, list):
            raise self._invalid_response("streaming tool_calls delta must be a list")
        for raw_call in value:
            if not isinstance(raw_call, dict):
                raise self._invalid_response("streaming tool call delta must be an object")
            index = raw_call.get("index")
            if isinstance(index, bool) or not isinstance(index, int) or index < 0:
                raise self._invalid_response(
                    "streaming tool call delta requires non-negative index"
                )
            current = target.setdefault(
                index,
                {"id": "", "function": {"name": "", "arguments": ""}},
            )

            call_id = raw_call.get("id")
            if isinstance(call_id, str) and call_id:
                current["id"] = call_id
            function = raw_call.get("function")
            if function is None:
                continue
            if not isinstance(function, dict):
                raise self._invalid_response("streaming tool call function delta must be an object")
            current_function = current["function"]
            assert isinstance(current_function, dict)
            name = function.get("name")
            if isinstance(name, str):
                current_function["name"] = f"{current_function.get('name', '')}{name}"
            arguments = function.get("arguments")
            if isinstance(arguments, str):
                current_function["arguments"] = (
                    f"{current_function.get('arguments', '')}{arguments}"
                )


def _is_json_value(value: object) -> bool:
    if value is None or isinstance(value, str | int | float | bool):
        return True
    if isinstance(value, list):
        return all(_is_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _is_json_value(item) for key, item in value.items())
    return False