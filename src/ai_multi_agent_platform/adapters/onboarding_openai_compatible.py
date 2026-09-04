"""OpenAI-compatible first-run setup adapter.

The concrete dependency lives under ``adapters`` so platform core remains provider-neutral.
Canonical secret references are resolved only immediately before the adapter performs an HTTP
request; secret material is never copied into ordinary model or onboarding configuration.
"""

from __future__ import annotations

from collections.abc import Mapping

from ai_multi_agent_platform.configuration import SecretAccessContext, SecretProvider
from ai_multi_agent_platform.contracts import (
    ContractError,
    ErrorCode,
    JsonValue,
    ModelProvider,
)
from ai_multi_agent_platform.onboarding.providers import (
    OnboardingModelAdapter,
    OnboardingModelEndpoint,
)
from ai_multi_agent_platform.security import SecretReference

from .openai_compatible import (
    HttpJsonResponse,
    OpenAICompatibleModelProvider,
    OpenAICompatibleProviderConfig,
    OpenAICompatibleTransport,
    UrllibOpenAICompatibleTransport,
)

OPENAI_COMPATIBLE_ONBOARDING_ADAPTER_ID = "openai-compatible"


class _SecretResolvingOpenAICompatibleTransport(OpenAICompatibleTransport):
    """Inject one short-lived canonical secret into one outbound adapter request."""

    def __init__(
        self,
        *,
        delegate: OpenAICompatibleTransport,
        secret_provider: SecretProvider,
        reference: SecretReference,
        provider_id: str,
    ) -> None:
        self.delegate = delegate
        self.secret_provider = secret_provider
        self.reference = reference
        self.provider_id = provider_id

    async def request_json(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, JsonValue] | None,
        timeout_seconds: float,
    ) -> HttpJsonResponse:
        material = await self.secret_provider.resolve(
            self.reference,
            SecretAccessContext(
                consumer_ref=f"model-provider:{self.provider_id}",
                action=f"http:{method.casefold()}",
                purpose="model-provider-auth",
                requested_lifetime_seconds=max(1, min(int(timeout_seconds) + 30, 3600)),
            ),
        )
        request_headers = dict(headers)
        request_headers["Authorization"] = f"Bearer {material.reveal()}"
        return await self.delegate.request_json(
            method,
            url,
            headers=request_headers,
            payload=payload,
            timeout_seconds=timeout_seconds,
        )


class OpenAICompatibleOnboardingAdapter(OnboardingModelAdapter):
    """Construct and validate the installed OpenAI-compatible ModelProvider adapter."""

    adapter_id = OPENAI_COMPATIBLE_ONBOARDING_ADAPTER_ID

    def __init__(
        self,
        *,
        transport: OpenAICompatibleTransport | None = None,
        secret_provider: SecretProvider | None = None,
    ) -> None:
        self.transport = transport
        self.secret_provider = secret_provider

    def build_provider(self, endpoint: OnboardingModelEndpoint) -> ModelProvider:
        transport = self.transport or UrllibOpenAICompatibleTransport()
        if endpoint.credential_ref is not None:
            if self.secret_provider is None:
                raise ContractError(
                    ErrorCode.INVALID_CONFIGURATION,
                    "model endpoint references a canonical secret but no SecretProvider is "
                    "installed for this onboarding adapter",
                    provider_id=endpoint.provider_id,
                )
            transport = _SecretResolvingOpenAICompatibleTransport(
                delegate=transport,
                secret_provider=self.secret_provider,
                reference=endpoint.credential_ref,
                provider_id=endpoint.provider_id,
            )
        return OpenAICompatibleModelProvider(
            OpenAICompatibleProviderConfig(
                provider_id=endpoint.provider_id,
                base_url=endpoint.base_url,
                models=endpoint.models,
            ),
            transport=transport,
        )

    async def list_native_models(self, provider: ModelProvider) -> tuple[str, ...]:
        if not isinstance(provider, OpenAICompatibleModelProvider):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "OpenAI-compatible onboarding adapter received the wrong ModelProvider type",
            )
        return await provider.list_native_models()
