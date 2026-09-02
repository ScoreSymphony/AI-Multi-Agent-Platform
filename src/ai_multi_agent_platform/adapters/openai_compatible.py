"""Local/self-hosted OpenAI-compatible model provider.

The adapter intentionally depends only on the Python standard library. It accepts
canonical model configuration IDs and resolves provider-native model names only
inside the adapter boundary.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from time import perf_counter
from types import MappingProxyType
from typing import Protocol
from urllib import error, request

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


@dataclass(frozen=True, slots=True)
class HttpJsonResponse:
    """Transport-neutral HTTP response used by the adapter tests."""

    status_code: int
    payload: JsonValue
    headers: Mapping[str, str] = field(default_factory=dict)


class OpenAICompatibleTransport(Protocol):
    """Minimal injectable transport so provider tests require no real server."""

    async def request_json(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, JsonValue] | None,
        timeout_seconds: float,
    ) -> HttpJsonResponse: ...


class UrllibOpenAICompatibleTransport:
    """Standard-library transport for local/self-hosted compatible endpoints."""

    async def request_json(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, JsonValue] | None,
        timeout_seconds: float,
    ) -> HttpJsonResponse:
        return await asyncio.to_thread(
            self._request_json_sync,
            method,
            url,
            headers,
            payload,
            timeout_seconds,
        )

    @staticmethod
    def _request_json_sync(
        method: str,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, JsonValue] | None,
        timeout_seconds: float,
    ) -> HttpJsonResponse:
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
                raw = response.read().decode("utf-8")
                parsed: JsonValue = json.loads(raw) if raw else None
                return HttpJsonResponse(
                    status_code=response.status,
                    payload=parsed,
                    headers=dict(response.headers.items()),
                )
        except error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                parsed = raw
            return HttpJsonResponse(
                status_code=exc.code,
                payload=parsed,
                headers=dict(exc.headers.items()) if exc.headers is not None else {},
            )


@dataclass(frozen=True, slots=True)
class OpenAICompatibleProviderConfig:
    """Configuration for one OpenAI-compatible provider instance.

    ``models`` maps stable canonical model configuration IDs to provider-native
    names. Secret values are never stored here; ``api_key_env`` is only a secret
    reference resolved at invocation time.
    """

    provider_id: str
    base_url: str
    models: Mapping[str, str]
    api_key_env: str | None = None
    timeout_seconds: float = 120.0
    extra_headers: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.provider_id.strip():
            raise ValueError("provider_id must not be blank")
        if not self.base_url.strip():
            raise ValueError("base_url must not be blank")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        if not self.models:
            raise ValueError("at least one canonical model mapping is required")
        if any(not key.strip() or not value.strip() for key, value in self.models.items()):
            raise ValueError("model mappings must contain non-blank IDs and native names")
        if self.api_key_env is not None and not self.api_key_env.strip():
            raise ValueError("api_key_env must not be blank when provided")

        object.__setattr__(self, "models", MappingProxyType(dict(self.models)))
        object.__setattr__(self, "extra_headers", MappingProxyType(dict(self.extra_headers)))


class OpenAICompatibleModelProvider(ModelProvider):
    """ModelProvider for local or self-hosted OpenAI-compatible HTTP APIs."""

    def __init__(
        self,
        config: OpenAICompatibleProviderConfig,
        *,
        transport: OpenAICompatibleTransport | None = None,
    ) -> None:
        self.config = config
        self.transport = transport or UrllibOpenAICompatibleTransport()
        self._health = HealthStatus.UNKNOWN

    @property
    def descriptor(self) -> ProviderDescriptor:
        credential_source = "none"
        if self.config.api_key_env is not None:
            credential_source = f"env:{self.config.api_key_env}"
        return ProviderDescriptor(
            provider_id=self.config.provider_id,
            provider_type="openai-compatible-model",
            supported_operations=("generate", "health", "list_native_models"),
            capabilities=(
                Capability(
                    name="model.openai-compatible.chat",
                    kind=CapabilityKind.MODEL,
                    supported_operations=("generate",),
                    modalities=("text",),
                    features=("local-first", "self-hosted", "structured-output"),
                ),
            ),
            health=self._health,
            available=True,
            adapter_metadata=(
                AdapterMetadata(
                    namespace="openai-compatible",
                    values={
                        "base_url": self.config.base_url,
                        "credential_source": credential_source,
                        "configured_model_count": len(self.config.models),
                    },
                ),
            ),
        )

    async def health(self) -> HealthStatus:
        try:
            response = await self._request("GET", "/models", payload=None)
        except ContractError:
            self._health = HealthStatus.UNAVAILABLE
            return self._health

        self._health = (
            HealthStatus.HEALTHY
            if 200 <= response.status_code < 300
            else HealthStatus.UNAVAILABLE
        )
        return self._health

    async def list_native_models(self) -> tuple[str, ...]:
        response = await self._request("GET", "/models", payload=None)
        self._raise_for_status(response)
        payload = response.payload
        if not isinstance(payload, dict):
            raise self._invalid_response("/models response must be a JSON object")
        data = payload.get("data")
        if not isinstance(data, list):
            raise self._invalid_response("/models response must contain a data list")

        model_ids: list[str] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            model_id = item.get("id")
            if isinstance(model_id, str) and model_id.strip():
                model_ids.append(model_id)
        return tuple(sorted(set(model_ids)))

    async def generate(self, request_data: ModelRequest) -> ModelResponse:
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
            "messages": [
                {"role": "user", "content": message} for message in request_data.messages
            ],
            "stream": False,
        }
        self._copy_generation_parameters(request_data, payload)
        if request_data.requirements.get("structured_output") is True:
            payload["response_format"] = {"type": "json_object"}

        started = perf_counter()
        response = await self._request("POST", "/chat/completions", payload=payload)
        elapsed_ms = (perf_counter() - started) * 1000.0
        self._raise_for_status(response)

        body = response.payload
        if not isinstance(body, dict):
            raise self._invalid_response("chat completion response must be a JSON object")
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise self._invalid_response("chat completion response must contain choices")
        first = choices[0]
        if not isinstance(first, dict):
            raise self._invalid_response("first chat completion choice must be an object")
        message = first.get("message")
        if not isinstance(message, dict):
            raise self._invalid_response("chat completion choice must contain message")
        content = message.get("content")
        if content is None:
            content = ""
        if not isinstance(content, str):
            raise self._invalid_response("chat completion message content must be text")

        usage = body.get("usage")
        canonical_usage: dict[str, JsonValue] = {}
        if isinstance(usage, dict):
            canonical_usage = {
                key: value
                for key, value in usage.items()
                if isinstance(key, str) and self._is_json_value(value)
            }

        finish_reason = first.get("finish_reason")
        metadata_values: dict[str, JsonValue] = {
            "provider_native_model": native_model,
            "correlation_id": request_data.context.correlation_id,
            "latency_ms": elapsed_ms,
        }
        if isinstance(finish_reason, str):
            metadata_values["finish_reason"] = finish_reason

        return ModelResponse(
            request_id=request_data.request_id,
            text=content,
            model_ref=canonical_model_id,
            usage=canonical_usage,
            adapter_metadata=(
                AdapterMetadata(
                    namespace="openai-compatible",
                    values=metadata_values,
                ),
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
            "model_config_id is required when a provider exposes multiple models",
            provider_id=self.config.provider_id,
        )

    def _copy_generation_parameters(
        self,
        request_data: ModelRequest,
        payload: dict[str, JsonValue],
    ) -> None:
        for key in ("temperature", "top_p", "max_tokens", "seed"):
            value = request_data.requirements.get(key)
            if value is not None:
                payload[key] = value

    async def _request(
        self,
        method: str,
        path: str,
        *,
        payload: Mapping[str, JsonValue] | None,
    ) -> HttpJsonResponse:
        timeout_seconds = self.config.timeout_seconds
        headers = self._headers()
        url = f"{self.config.base_url.rstrip('/')}{path}"
        try:
            async with asyncio.timeout(timeout_seconds):
                return await self.transport.request_json(
                    method,
                    url,
                    headers=headers,
                    payload=payload,
                    timeout_seconds=timeout_seconds,
                )
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

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            **dict(self.config.extra_headers),
        }
        if self.config.api_key_env is None:
            return headers

        api_key = os.environ.get(self.config.api_key_env)
        if api_key is None or not api_key.strip():
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                f"credential environment variable is not set: {self.config.api_key_env}",
                provider_id=self.config.provider_id,
            )
        headers["Authorization"] = f"Bearer {api_key}"
        return headers

    def _raise_for_status(self, response: HttpJsonResponse) -> None:
        status = response.status_code
        if 200 <= status < 300:
            return

        code = ErrorCode.BACKEND_ERROR
        retryable = False
        if status == 400:
            code = ErrorCode.INVALID_REQUEST
        elif status == 401:
            code = ErrorCode.UNAUTHORIZED
        elif status == 403:
            code = ErrorCode.FORBIDDEN
        elif status == 404:
            code = ErrorCode.MODEL_UNAVAILABLE
        elif status in {408, 504}:
            code = ErrorCode.TIMEOUT
            retryable = True
        elif status == 413:
            code = ErrorCode.INPUT_TOO_LARGE
        elif status == 429:
            code = ErrorCode.RATE_LIMITED
            retryable = True
        elif status >= 500:
            code = ErrorCode.TRANSIENT_FAILURE
            retryable = True

        raise ContractError(
            code,
            f"model provider returned HTTP {status}",
            retryable=retryable,
            provider_id=self.config.provider_id,
            details={"http_status": status},
        )

    def _invalid_response(self, message: str) -> ContractError:
        return ContractError(
            ErrorCode.INVALID_PROVIDER_RESPONSE,
            message,
            provider_id=self.config.provider_id,
        )

    @classmethod
    def _is_json_value(cls, value: object) -> bool:
        if value is None or isinstance(value, str | int | float | bool):
            return True
        if isinstance(value, list):
            return all(cls._is_json_value(item) for item in value)
        if isinstance(value, dict):
            return all(
                isinstance(key, str) and cls._is_json_value(item)
                for key, item in value.items()
            )
        return False
