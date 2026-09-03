"""Optional LiteLLM model-gateway adapter for issue #11.

LiteLLM is deliberately kept behind the platform-owned ``ModelProvider``
contract.  Importing the platform (or this module) does not import LiteLLM;
the optional Python dependency is loaded only when library mode is used.

Proxy mode reuses the existing OpenAI-compatible transport because a LiteLLM
Proxy exposes an OpenAI-compatible HTTP surface.  Platform routing remains
canonical and happens before this adapter is selected.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from importlib import metadata
from time import perf_counter, time
from types import MappingProxyType
from typing import cast

from ai_multi_agent_platform.contracts import (
    AdapterMetadata,
    Capability,
    CapabilityKind,
    ContractError,
    ErrorCode,
    HealthStatus,
    JsonValue,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ProviderDescriptor,
)

from .openai_compatible import (
    OpenAICompatibleModelProvider,
    OpenAICompatibleProviderConfig,
    OpenAICompatibleTransport,
)

LiteLLMCompletion = Callable[..., Awaitable[object]]


class LiteLLMMode(StrEnum):
    """Supported first-class LiteLLM integration modes."""

    LIBRARY = "library"
    PROXY = "proxy"


@dataclass(frozen=True, slots=True)
class LiteLLMProviderConfig:
    """Configuration for one optional LiteLLM provider instance.

    ``models`` maps stable canonical model configuration IDs to the model name
    understood by LiteLLM (library mode) or by the LiteLLM Proxy (proxy mode).
    Credentials are references to environment variables, never secret values.
    """

    provider_id: str
    mode: LiteLLMMode
    models: Mapping[str, str]
    enabled: bool = True
    base_url: str | None = None
    api_key_env: str | None = None
    timeout_seconds: float = 120.0
    extra_headers: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.provider_id.strip():
            raise ValueError("provider_id must not be blank")
        if not self.models:
            raise ValueError("at least one canonical model mapping is required")
        if any(not key.strip() or not value.strip() for key, value in self.models.items()):
            raise ValueError("model mappings must contain non-blank IDs and LiteLLM names")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        if self.base_url is not None and not self.base_url.strip():
            raise ValueError("base_url must not be blank when provided")
        if self.mode is LiteLLMMode.PROXY and self.base_url is None:
            raise ValueError("proxy mode requires base_url")
        if self.api_key_env is not None and not self.api_key_env.strip():
            raise ValueError("api_key_env must not be blank when provided")
        if any(not key.strip() or not value.strip() for key, value in self.extra_headers.items()):
            raise ValueError("extra_headers must contain non-blank names and values")

        object.__setattr__(self, "models", MappingProxyType(dict(self.models)))
        object.__setattr__(self, "extra_headers", MappingProxyType(dict(self.extra_headers)))


class LiteLLMModelProvider(ModelProvider):
    """Optional ``ModelProvider`` backed by LiteLLM library or Proxy mode."""

    def __init__(
        self,
        config: LiteLLMProviderConfig,
        *,
        completion: LiteLLMCompletion | None = None,
        proxy_transport: OpenAICompatibleTransport | None = None,
    ) -> None:
        self.config = config
        self._completion = completion
        self._health = HealthStatus.UNKNOWN
        self._proxy: OpenAICompatibleModelProvider | None = None
        if config.mode is LiteLLMMode.PROXY:
            assert config.base_url is not None
            self._proxy = OpenAICompatibleModelProvider(
                OpenAICompatibleProviderConfig(
                    provider_id=config.provider_id,
                    base_url=config.base_url,
                    models=config.models,
                    api_key_env=config.api_key_env,
                    timeout_seconds=config.timeout_seconds,
                    extra_headers=config.extra_headers,
                ),
                transport=proxy_transport,
            )

    @property
    def descriptor(self) -> ProviderDescriptor:
        version = self._installed_version()
        metadata_values: dict[str, JsonValue] = {
            "mode": self.config.mode.value,
            "configured_model_count": len(self.config.models),
            "enabled": self.config.enabled,
            "dependency": "optional",
        }
        if self.config.base_url is not None:
            metadata_values["base_url"] = self.config.base_url
        if version is not None:
            metadata_values["litellm_version"] = version
        if self.config.api_key_env is not None:
            metadata_values["credential_source"] = f"env:{self.config.api_key_env}"
        else:
            metadata_values["credential_source"] = "none"

        return ProviderDescriptor(
            provider_id=self.config.provider_id,
            provider_type="litellm-model-gateway",
            supported_operations=("generate", "health"),
            capabilities=(
                Capability(
                    name="model.litellm.chat",
                    kind=CapabilityKind.MODEL,
                    supported_operations=("generate",),
                    modalities=("text",),
                    features=(
                        "optional-adapter",
                        self.config.mode.value,
                        "tool-calling",
                        "structured-output",
                        "local-compatible",
                    ),
                ),
            ),
            health=self._health,
            available=self.config.enabled,
            adapter_metadata=(AdapterMetadata(namespace="litellm", values=metadata_values),),
        )

    async def health(self) -> HealthStatus:
        if not self.config.enabled:
            self._health = HealthStatus.UNAVAILABLE
            return self._health

        if self.config.mode is LiteLLMMode.PROXY:
            assert self._proxy is not None
            self._health = await self._proxy.health()
            return self._health

        try:
            self._resolve_completion()
            self._resolve_api_key()
        except ContractError:
            self._health = HealthStatus.UNAVAILABLE
        else:
            self._health = HealthStatus.HEALTHY
        return self._health

    async def generate(self, request_data: ModelRequest) -> ModelResponse:
        if not self.config.enabled:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "LiteLLM adapter is disabled",
                provider_id=self.config.provider_id,
            )

        if self.config.mode is LiteLLMMode.PROXY:
            assert self._proxy is not None
            response = await self._proxy.generate(request_data)
            return self._decorate_proxy_response(response, request_data)

        return await self._generate_library(request_data)

    async def _generate_library(self, request_data: ModelRequest) -> ModelResponse:
        canonical_model_id = self._canonical_model_id(request_data)
        native_model = self.config.models.get(canonical_model_id)
        if native_model is None:
            raise ContractError(
                ErrorCode.MODEL_UNAVAILABLE,
                f"model is not configured for LiteLLM provider: {canonical_model_id}",
                provider_id=self.config.provider_id,
                details={"model_config_id": canonical_model_id},
            )

        payload: dict[str, object] = {
            "model": native_model,
            "messages": self._messages(request_data),
            "stream": False,
        }
        for key in ("temperature", "top_p", "max_tokens", "seed", "stop"):
            value = request_data.requirements.get(key)
            if value is not None:
                payload[key] = value

        tools = self._tools(request_data)
        if tools:
            payload["tools"] = tools
        response_format = self._response_format(request_data)
        if response_format is not None:
            payload["response_format"] = response_format
        if self.config.base_url is not None:
            payload["api_base"] = self.config.base_url
        api_key = self._resolve_api_key()
        if api_key is not None:
            payload["api_key"] = api_key
        if self.config.extra_headers:
            payload["extra_headers"] = dict(self.config.extra_headers)

        timeout_seconds = (
            request_data.context.control.timeout_seconds or self.config.timeout_seconds
        )
        payload["timeout"] = timeout_seconds
        completion = self._resolve_completion()

        started_wall_ms = time() * 1000.0
        started = perf_counter()
        try:
            raw_response = await asyncio.wait_for(
                completion(**payload),
                timeout=timeout_seconds,
            )
        except asyncio.CancelledError as exc:
            raise ContractError(
                ErrorCode.CANCELLED,
                "LiteLLM model request was cancelled",
                provider_id=self.config.provider_id,
            ) from exc
        except TimeoutError as exc:
            raise ContractError(
                ErrorCode.TIMEOUT,
                "LiteLLM model request timed out",
                retryable=True,
                provider_id=self.config.provider_id,
            ) from exc
        except ContractError:
            raise
        except Exception as exc:
            raise self._map_exception(exc) from exc

        elapsed_ms = (perf_counter() - started) * 1000.0
        ended_wall_ms = time() * 1000.0
        response = self._response_mapping(raw_response)
        content, finish_reason, usage, raw_tool_calls = self._parse_response(response)
        canonical_tool_calls = self._canonical_tool_calls(raw_tool_calls)
        structured_output = self._structured_output(request_data, content)

        protocol_values: dict[str, JsonValue] = {
            "correlation_id": request_data.context.correlation_id,
            "latency_ms": elapsed_ms,
            "started_unix_ms": started_wall_ms,
            "ended_unix_ms": ended_wall_ms,
            "finish_reason": finish_reason,
        }
        self._copy_context_metadata(request_data, protocol_values)
        if canonical_tool_calls:
            protocol_values["tool_calls"] = canonical_tool_calls
        if structured_output is not None:
            protocol_values["structured_output"] = structured_output

        adapter_values: dict[str, JsonValue] = {
            "mode": LiteLLMMode.LIBRARY.value,
            "provider_native_model": native_model,
            "correlation_id": request_data.context.correlation_id,
            "latency_ms": elapsed_ms,
            "status": "success",
        }
        self._copy_context_metadata(request_data, adapter_values)
        version = self._installed_version()
        if version is not None:
            adapter_values["litellm_version"] = version

        return ModelResponse(
            request_id=request_data.request_id,
            text=content,
            model_ref=canonical_model_id,
            usage=usage,
            adapter_metadata=(
                AdapterMetadata(namespace="litellm", values=adapter_values),
                AdapterMetadata(namespace="model-protocol", values=protocol_values),
            ),
        )

    def _canonical_model_id(self, request_data: ModelRequest) -> str:
        explicit = request_data.requirements.get("model_config_id")
        if explicit is not None:
            if not isinstance(explicit, str) or not explicit.strip():
                raise ContractError(
                    ErrorCode.INVALID_REQUEST,
                    "model_config_id must be a non-blank canonical string",
                    provider_id=self.config.provider_id,
                )
            return explicit
        if len(self.config.models) == 1:
            return next(iter(self.config.models))
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "model_config_id is required when a LiteLLM provider exposes multiple models",
            provider_id=self.config.provider_id,
        )

    def _messages(self, request_data: ModelRequest) -> list[dict[str, object]]:
        canonical = request_data.requirements.get("canonical_messages")
        if canonical is None:
            return [{"role": "user", "content": message} for message in request_data.messages]
        if not isinstance(canonical, list):
            raise self._invalid_request("canonical_messages must be a list")

        messages: list[dict[str, object]] = []
        for raw_message in canonical:
            if not isinstance(raw_message, dict):
                raise self._invalid_request("canonical message must be an object")
            role = raw_message.get("role")
            content = raw_message.get("content")
            if not isinstance(role, str) or not isinstance(content, list):
                raise self._invalid_request("canonical message requires role and content")
            text_parts: list[str] = []
            for block in content:
                if not isinstance(block, dict):
                    raise self._invalid_request("canonical content block must be an object")
                kind = block.get("kind")
                if kind == "text":
                    text = block.get("text")
                    if not isinstance(text, str):
                        raise self._invalid_request("text content block requires text")
                    text_parts.append(text)
                elif kind == "json":
                    value = block.get("value")
                    if not self._is_json_value(value):
                        raise self._invalid_request("JSON content block contains invalid data")
                    text_parts.append(json.dumps(value, separators=(",", ":")))
                else:
                    raise ContractError(
                        ErrorCode.UNSUPPORTED_CAPABILITY,
                        f"content block kind is not supported by LiteLLM adapter: {kind}",
                        provider_id=self.config.provider_id,
                    )

            translated: dict[str, object] = {
                "role": role,
                "content": "\n".join(text_parts),
            }
            name = raw_message.get("name")
            if isinstance(name, str):
                translated["name"] = name
            tool_call_id = raw_message.get("tool_call_id")
            if isinstance(tool_call_id, str):
                translated["tool_call_id"] = tool_call_id
            messages.append(translated)
        return messages

    def _tools(self, request_data: ModelRequest) -> list[dict[str, object]]:
        raw_tools = request_data.requirements.get("canonical_tools")
        if raw_tools is None:
            return []
        if not isinstance(raw_tools, list):
            raise self._invalid_request("canonical_tools must be a list")

        tools: list[dict[str, object]] = []
        for raw_tool in raw_tools:
            if not isinstance(raw_tool, dict):
                raise self._invalid_request("canonical tool definition must be an object")
            name = raw_tool.get("name")
            description = raw_tool.get("description", "")
            input_schema = raw_tool.get("input_schema")
            if not isinstance(name, str) or not isinstance(description, str):
                raise self._invalid_request("canonical tool requires string name/description")
            if not isinstance(input_schema, dict) or not self._is_json_value(input_schema):
                raise self._invalid_request("canonical tool input_schema must be a JSON object")
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": description,
                        "parameters": input_schema,
                    },
                }
            )
        return tools

    def _response_format(self, request_data: ModelRequest) -> dict[str, object] | None:
        expectation = request_data.requirements.get("response_expectation")
        if expectation is None:
            return None
        if not isinstance(expectation, dict):
            raise self._invalid_request("response_expectation must be an object")
        kind = expectation.get("kind", "text")
        if kind == "text":
            return None
        if kind == "json_object":
            return {"type": "json_object"}
        if kind == "json_schema":
            schema = expectation.get("json_schema")
            schema_name = expectation.get("schema_name")
            strict = expectation.get("strict", False)
            if not isinstance(schema, dict) or not self._is_json_value(schema):
                raise self._invalid_request("json_schema response requires a JSON object schema")
            if not isinstance(schema_name, str) or not schema_name.strip():
                raise self._invalid_request("json_schema response requires schema_name")
            if not isinstance(strict, bool):
                raise self._invalid_request("json_schema strict must be a boolean")
            return {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "schema": schema,
                    "strict": strict,
                },
            }
        raise ContractError(
            ErrorCode.UNSUPPORTED_CAPABILITY,
            f"unsupported structured response kind: {kind}",
            provider_id=self.config.provider_id,
        )

    def _parse_response(
        self,
        response: Mapping[str, object],
    ) -> tuple[str, str, dict[str, JsonValue], object]:
        choices = response.get("choices")
        if not isinstance(choices, list) or not choices:
            raise self._invalid_response("LiteLLM response must contain choices")
        first = choices[0]
        if not isinstance(first, Mapping):
            raise self._invalid_response("first LiteLLM choice must be an object")
        message = first.get("message")
        if not isinstance(message, Mapping):
            raise self._invalid_response("LiteLLM choice must contain message")
        content = message.get("content")
        if content is None:
            content = ""
        if not isinstance(content, str):
            raise self._invalid_response("LiteLLM message content must be text")

        finish_reason = first.get("finish_reason")
        normalized_finish = self._normalize_finish_reason(finish_reason)
        usage = self._usage(response.get("usage"))
        return content, normalized_finish, usage, message.get("tool_calls")

    def _decorate_proxy_response(
        self,
        response: ModelResponse,
        request_data: ModelRequest,
    ) -> ModelResponse:
        values: dict[str, JsonValue] = {
            "mode": LiteLLMMode.PROXY.value,
            "correlation_id": request_data.context.correlation_id,
            "status": "success",
        }
        self._copy_context_metadata(request_data, values)
        return ModelResponse(
            request_id=response.request_id,
            text=response.text,
            model_ref=response.model_ref,
            usage=dict(response.usage),
            adapter_metadata=response.adapter_metadata
            + (AdapterMetadata(namespace="litellm", values=values),),
        )

    def _resolve_completion(self) -> LiteLLMCompletion:
        if self._completion is not None:
            return self._completion
        try:
            module = importlib.import_module("litellm")
        except ModuleNotFoundError as exc:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "LiteLLM library mode is enabled but the optional "
                "'litellm' dependency is not installed",
                provider_id=self.config.provider_id,
                details={"install_extra": "ai-multi-agent-platform[litellm]"},
            ) from exc
        completion = getattr(module, "acompletion", None)
        if not callable(completion):
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "installed LiteLLM package does not expose callable acompletion",
                provider_id=self.config.provider_id,
            )
        return cast(LiteLLMCompletion, completion)

    def _resolve_api_key(self) -> str | None:
        if self.config.api_key_env is None:
            return None
        value = os.environ.get(self.config.api_key_env)
        if value is None or not value:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "configured LiteLLM credential environment variable is missing: "
                f"{self.config.api_key_env}",
                provider_id=self.config.provider_id,
                details={"credential_source": f"env:{self.config.api_key_env}"},
            )
        return value

    def _map_exception(self, exc: Exception) -> ContractError:
        name = type(exc).__name__
        lowered = name.lower()
        code = ErrorCode.BACKEND_ERROR
        retryable = False
        if "authentication" in lowered or "permission" in lowered:
            code = ErrorCode.UNAUTHORIZED
        elif "contextwindow" in lowered or "context_length" in lowered:
            code = ErrorCode.INPUT_TOO_LARGE
        elif "ratelimit" in lowered or "rate_limit" in lowered:
            code = ErrorCode.RATE_LIMITED
            retryable = True
        elif "timeout" in lowered:
            code = ErrorCode.TIMEOUT
            retryable = True
        elif "notfound" in lowered or "modelnotfound" in lowered:
            code = ErrorCode.MODEL_UNAVAILABLE
        elif "connection" in lowered or "serviceunavailable" in lowered:
            code = ErrorCode.UNAVAILABLE
            retryable = True
        elif "unsupported" in lowered:
            code = ErrorCode.UNSUPPORTED_CAPABILITY
        elif "badrequest" in lowered or "invalidrequest" in lowered:
            code = ErrorCode.INVALID_REQUEST
        elif "apierror" in lowered or "temporary" in lowered or "transient" in lowered:
            code = ErrorCode.TRANSIENT_FAILURE
            retryable = True

        return ContractError(
            code,
            f"LiteLLM request failed: {name}",
            retryable=retryable,
            provider_id=self.config.provider_id,
            details={"exception_type": name},
            adapter_metadata=(
                AdapterMetadata(
                    namespace="litellm",
                    values={"exception_type": name, "mode": self.config.mode.value},
                ),
            ),
        )

    def _structured_output(self, request_data: ModelRequest, content: str) -> JsonValue:
        expectation = request_data.requirements.get("response_expectation")
        if not isinstance(expectation, dict):
            return None
        kind = expectation.get("kind", "text")
        if kind == "text":
            return None
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise self._invalid_response("structured LiteLLM response is not valid JSON") from exc
        if not self._is_json_value(parsed):
            raise self._invalid_response("structured LiteLLM response is not JSON-compatible")
        return parsed

    def _canonical_tool_calls(self, value: object) -> list[JsonValue]:
        if not isinstance(value, list):
            return []
        parsed: list[JsonValue] = []
        for item in value:
            if not isinstance(item, Mapping):
                continue
            call_id = item.get("id")
            function = item.get("function")
            if not isinstance(call_id, str) or not isinstance(function, Mapping):
                continue
            name = function.get("name")
            arguments = function.get("arguments")
            if not isinstance(name, str):
                continue
            argument_object: dict[str, JsonValue]
            if isinstance(arguments, str):
                try:
                    decoded = json.loads(arguments)
                except json.JSONDecodeError:
                    continue
                if not isinstance(decoded, dict) or not self._is_json_value(decoded):
                    continue
                argument_object = decoded
            elif isinstance(arguments, dict) and self._is_json_value(arguments):
                argument_object = arguments
            else:
                continue
            parsed.append(
                {
                    "call_id": call_id,
                    "tool_name": name,
                    "arguments": argument_object,
                }
            )
        return parsed

    def _usage(self, value: object) -> dict[str, JsonValue]:
        if not isinstance(value, Mapping):
            return {}
        result: dict[str, JsonValue] = {}
        for key, item in value.items():
            if isinstance(key, str) and self._is_json_value(item):
                result[key] = item
        return result

    def _response_mapping(self, value: object) -> Mapping[str, object]:
        if isinstance(value, Mapping):
            return cast(Mapping[str, object], value)
        model_dump = getattr(value, "model_dump", None)
        if callable(model_dump):
            dumped = model_dump()
            if isinstance(dumped, Mapping):
                return cast(Mapping[str, object], dumped)
        raise self._invalid_response("LiteLLM response is not mapping-compatible")

    def _normalize_finish_reason(self, value: object) -> str:
        if not isinstance(value, str):
            return "unknown"
        return {
            "stop": "stop",
            "length": "length",
            "tool_calls": "tool_call",
            "function_call": "tool_call",
            "content_filter": "content_filter",
        }.get(value, "unknown")

    def _copy_context_metadata(
        self,
        request_data: ModelRequest,
        values: dict[str, JsonValue],
    ) -> None:
        for key in ("task_id", "run_id", "agent_id"):
            value = request_data.requirements.get(key)
            if isinstance(value, str):
                values[key] = value

    def _invalid_request(self, message: str) -> ContractError:
        return ContractError(
            ErrorCode.INVALID_REQUEST,
            message,
            provider_id=self.config.provider_id,
        )

    def _invalid_response(self, message: str) -> ContractError:
        return ContractError(
            ErrorCode.INVALID_PROVIDER_RESPONSE,
            message,
            provider_id=self.config.provider_id,
        )

    @staticmethod
    def _is_json_value(value: object) -> bool:
        if value is None or isinstance(value, str | int | float | bool):
            return True
        if isinstance(value, list):
            return all(LiteLLMModelProvider._is_json_value(item) for item in value)
        if isinstance(value, dict):
            return all(
                isinstance(key, str) and LiteLLMModelProvider._is_json_value(item)
                for key, item in value.items()
            )
        return False

    @staticmethod
    def _installed_version() -> str | None:
        try:
            return metadata.version("litellm")
        except metadata.PackageNotFoundError:
            return None
