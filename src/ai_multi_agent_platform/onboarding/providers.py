"""Provider-neutral model setup seam used by first-run onboarding."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from ai_multi_agent_platform.contracts import ModelProvider
from ai_multi_agent_platform.security import SecretReference


@dataclass(frozen=True, slots=True)
class OnboardingModelEndpoint:
    """Value-free endpoint metadata passed to an explicitly installed adapter."""

    provider_id: str
    base_url: str
    models: Mapping[str, str]
    credential_ref: SecretReference | None = None


class OnboardingModelAdapter(Protocol):
    """Adapter-owned bridge from safe endpoint metadata to a canonical ModelProvider."""

    @property
    def adapter_id(self) -> str: ...

    def build_provider(self, endpoint: OnboardingModelEndpoint) -> ModelProvider: ...

    async def list_native_models(self, provider: ModelProvider) -> tuple[str, ...]: ...
